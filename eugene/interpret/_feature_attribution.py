import numpy as np
import torch
from captum.attr import InputXGradient, DeepLift, GradientShap
from ..utils import track
from ..preprocessing import dinuc_shuffle_seq
from tqdm.auto import tqdm
from yuzu.naive_ism import naive_ism
from ..preprocessing import perturb_seqs


@torch.inference_mode()
def _naive_ism(model, X_0, batch_size=128, device='cpu'):
    """In-silico mutagenesis saliency scores.
    This function will perform in-silico mutagenesis in a naive manner, i.e.,
    where each input sequence has a single mutation in it and the entirety
    of the sequence is run through the given model. It returns the ISM score,
    which is a vector of the L2 difference between the reference sequence
    and the perturbed sequences with one value for each output of the model.
    Parameters
    ----------
    model: torch.nn.Module
        The model to use.
    X_0: numpy.ndarray
        The one-hot encoded sequence to calculate saliency for.
    batch_size: int, optional
        The size of the batches.
    device: str, optional
        Whether to use a 'cpu' or 'gpu'.
    Returns
    -------
    X_ism: numpy.ndarray
        The saliency score for each perturbation.
    """

    n_seqs, n_choices, seq_len = X_0.shape
    X_idxs = X_0.argmax(axis=1)

    X = perturb_seqs(X_0)
    X_0 = torch.from_numpy(X_0)

    if device[:4] != str(next(model.parameters()).device):
        model = model.to(device)

    if device[:4] != X_0.device:
        X_0 = X_0.to(device)

    model = model.eval()
    reference = model(X_0).unsqueeze(1)

    starts = np.arange(0, X.shape[1], batch_size)
    isms = []
    for i in range(n_seqs):
        X = perturb_seqs(X_0[i])
        y = []

        for start in starts:
            X_ = X[0, start:start+batch_size]
            if device[:4] == 'cuda':
                X_ = X_.to(device)

            y_ = model(X_)
            y.append(y_)
            del X_

        y = torch.cat(y)

        ism = torch.square(y - reference[i]).sum(axis=-1)
        if len(ism.shape) == 2:
            ism = ism.sum(axis=-1)
        ism = torch.sqrt(ism)
        isms.append(ism)

        if device[:4] == 'cuda':
            torch.cuda.synchronize()
            torch.cuda.empty_cache()

    isms = torch.stack(isms)
    isms = isms.reshape(n_seqs, seq_len, n_choices-1)

    j_idxs = torch.arange(n_seqs*seq_len)
    X_ism = torch.zeros(n_seqs*seq_len, n_choices, device=device)
    for i in range(1, n_choices):
        i_idxs = (X_idxs.flatten() + i) % n_choices
        X_ism[j_idxs, i_idxs] = isms[:, :, i-1].flatten()

    X_ism = X_ism.reshape(n_seqs, seq_len, n_choices).permute(0, 2, 1)

    if device[:4] == 'cuda':
        X_ism = X_ism.cpu()

    X_ism = X_ism.numpy()
    return X_ism


def _ism_explain(model, inputs, ism_type="naive", device="cpu", batch_size=1):
    if ism_type == "naive":
        inputs = inputs[0].requires_grad_().detach().cpu().numpy()
        # Eventually make it capable of working with ds and ts
        attrs = naive_ism(model=model, X_0=inputs, device=device, batch_size=batch_size)
    else:
        raise ValueError("ISM type not supported")
    return attrs


def _grad_explain(model, inputs, ref_type=None, target=None, device="cpu"):
    model.train()
    model.to(device)
    grad_explainer = InputXGradient(model)
    forward_inputs = inputs[0].requires_grad_().to(device)
    reverse_inputs = inputs[1].requires_grad_().to(device)
    attrs = grad_explainer.attribute(forward_inputs, additional_forward_args=reverse_inputs)
    return attrs.to("cpu").detach().numpy()


def _deeplift_explain(model, inputs, ref_type="zero", target=None, device="cpu"):
    model.train()
    model.to(device)
    deep_lift = DeepLift(model)
    forward_inputs = inputs[0].requires_grad_().to(device)
    reverse_inputs = inputs[1].requires_grad_().to(device)
    if ref_type == "zero":
        forward_ref = torch.zeros(inputs[0].size()).to(device)
        reverse_ref = torch.zeros(inputs[1].size()).to(device)
    elif ref_type == "shuffle":
        forward_shuffled = forward_inputs.detach().to("cpu").squeeze(dim=0).numpy()
        forward_shuffled = reverse_inputs.detach().to("cpu").squeeze(dim=0).numpy()
        forward_ref = torch.tensor(dinuc_shuffle_seq(forward_shuffled)).unsqueeze(dim=0).requires_grad_().to(device)
        reverse_ref = torch.tensor(dinuc_shuffle_seq(reverse_ref)).unsqueeze(dim=0).requires_grad_().to(device)
    elif ref_type == "gc":
        ref = torch.tensor([0.3, 0.2, 0.2, 0.3]).expand(forward_inputs.size()[1], 4).unsqueeze(dim=0).to(device)
    attrs = deep_lift.attribute(inputs=forward_inputs, baselines=forward_ref, additional_forward_args=reverse_inputs)
    return attrs.to("cpu").detach().numpy()


def _gradientshap_explain(model, inputs, ref_type="zero", target=None, device="cpu"):
    model.train()
    model.to(device)
    gradient_shap = GradientShap(model)
    forward_inputs = inputs[0].requires_grad_().to(device)
    reverse_inputs = inputs[1].requires_grad_().to(device)
    if ref_type == "zero":
        forward_ref = torch.zeros(inputs[0].size()).to(device)
        reverse_ref = torch.zeros(inputs[1].size()).to(device)
    elif ref_type == "shuffle":
        forward_shuffled = forward_inputs.detach().to("cpu").squeeze(dim=0).numpy()
        forward_shuffled = reverse_inputs.detach().to("cpu").squeeze(dim=0).numpy()
        forward_ref = torch.tensor(dinuc_shuffle_seq(forward_shuffled)).unsqueeze(dim=0).requires_grad_().to(device)
        reverse_ref = torch.tensor(dinuc_shuffle_seq(reverse_ref)).unsqueeze(dim=0).requires_grad_().to(device)
    elif ref_type == "gc":
        ref = torch.tensor([0.3, 0.2, 0.2, 0.3]).expand(forward_inputs.size()[1], 4).unsqueeze(dim=0).to(device)
    attrs = gradient_shap.attribute(inputs=forward_inputs, baselines=forward_ref, additional_forward_args=reverse_inputs)
    return attrs.to("cpu").detach().numpy()


def nn_explain(model,
               inputs,
               saliency_type,
               target=None,
               ref_type="zero",
               device="cpu",
               batch_size=1,
               abs_value=False):
    if saliency_type == "DeepLift":
        attrs = _deeplift_explain(model=model, inputs=inputs, ref_type=ref_type, device=device, target=target)
    elif saliency_type == "InputXGradient":
        attrs = _grad_explain(model=model, inputs=inputs, ref_type=ref_type, device=device, target=target)
    elif saliency_type == "NaiveISM":
        attrs = _ism_explain(model=model, inputs=inputs, ism_type="naive", device=device, batch_size=batch_size)
    elif saliency_type == "GradientSHAP":
        attrs = _gradientshap_explain(model=model, inputs=inputs, ref_type=ref_type, device=device, target=target)
    else:
        raise ValueError("Saliency type not supported")
    if abs_value:
        attrs = np.abs(attrs)
    return attrs


@track
def feature_attribution(model, sdata, batch_size=32, saliency_method="InputXGradient", device="cpu", copy=False):
    if saliency_method == "NaiveISM":
        print("Note: NaiveISM is not implemented yet for models other than single stranded ones")
    sdata = sdata.copy() if copy else sdata
    sdataset = sdata.to_dataset(label=None, seq_transforms=["one_hot_encode"], transform_kwargs={"transpose": True})
    sdataloader = sdataset.to_dataloader(batch_size=batch_size)
    dataset_len = len(sdataloader.dataset)
    example_shape = sdataloader.dataset[0][1].numpy().shape
    all_explanations = np.zeros((dataset_len, *example_shape))
    if batch_size == None:
        batch_size = sdataloader.batch_size
    for i_batch, batch in tqdm(enumerate(sdataloader), total=int(dataset_len / batch_size)):
        ID, x, x_rev_comp, y = batch
        curr_explanations = nn_explain(model, (x, x_rev_comp), saliency_type=saliency_method, device=device, batch_size=batch_size)
        if (i_batch+1)*batch_size < dataset_len:
            all_explanations[i_batch*batch_size: (i_batch+1)*batch_size] = curr_explanations
        else:
            all_explanations[i_batch*batch_size:dataset_len] = curr_explanations
    sdata.uns[f"{saliency_method}_imps"] = all_explanations
    return sdata if copy else None
