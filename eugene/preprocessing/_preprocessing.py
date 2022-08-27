from tqdm.auto import tqdm
from ..dataloading import SeqData
from ._dataset_preprocess import split_train_test, binarize_values
from ._seq_preprocess import sanitize_seqs, ohe_alphabet_seqs, reverse_complement_seqs
from ..utils._decorators import track


# @track
def sanitize_sdata(sdata: SeqData, copy=False) -> SeqData:
    """Reverse complement sequences.
    Parameters
    ----------
    sdata : SeqData
        SeqData object.
    Returns
    -------
    SeqData
        SeqData object with reverse complement sequences.
    """
    sdata = sdata.copy() if copy else sdata
    sdata.seqs = sanitize_seqs(sdata.seqs) if sdata.seqs is not None else None
    sdata.rev_seqs = (
        sanitize_seqs(sdata.rev_seqs) if sdata.rev_seqs is not None else None
    )
    return sdata if copy else None


@track
def reverse_complement_data(sdata: SeqData, alphabet="DNA", copy=False) -> SeqData:
    """Reverse complement sequences.
    Parameters
    ----------
    sdata : SeqData
        SeqData object.
    RNAbases : bool
        Whether or not to use RNA bases in place of DNA bases. Default is false.
    Returns
    -------
    SeqData
        SeqData object with reverse complement sequences.
    """
    sdata = sdata.copy() if copy else sdata
    sdata.rev_seqs = reverse_complement_seqs(sdata.seqs, alphabet)
    return sdata if copy else None


@track
def one_hot_encode_data(
    sdata: SeqData,
    alphabet="DNA",
    seq_align="start",
    maxlen=None,
    fill_value=None,
    copy=False,
    **kwargs,
) -> SeqData:
    """One-hot encode sequences.
    Parameters
    ----------
    sdata : SeqData
        SeqData object.
    RNAbases : bool
        Whether or not to use RNA bases in place of DNA bases. Default is false.
    Returns
    -------
    SeqData
        SeqData object with one-hot encoded sequences.
    """
    sdata = sdata.copy() if copy else sdata
    if sdata.seqs is not None and sdata.ohe_seqs is None:
        sdata.ohe_seqs = ohe_alphabet_seqs(
            sdata.seqs,
            alphabet,
            seq_align=seq_align,
            maxlen=maxlen,
            fill_value=fill_value,
            **kwargs,
        )
    if sdata.rev_seqs is not None:
        sdata.ohe_rev_seqs = ohe_alphabet_seqs(
            sdata.rev_seqs,
            alphabet,
            seq_align=seq_align,
            maxlen=maxlen,
            fill_value=fill_value,
            **kwargs,
        )
    return sdata if copy else None


@track
def train_test_split_data(
    sdata: SeqData, train_key="train", chr=None, copy=False, **kwargs
) -> SeqData:
    """Train test split.
    Parameters
    ----------
    sdata : SeqData"""
    sdata = sdata.copy() if copy else sdata
    if chr is not None:
        chr = [chr] if isinstance(chr, str) else chr
        sdata[train_key] = ~sdata["chr"].isin(chr)
        return sdata if copy else None
    else:
        train_indeces, _, _, _ = split_train_test(
            X_data=sdata.seqs_annot.index, y_data=sdata.seqs_annot.index, **kwargs
        )
        sdata[train_key] = sdata.seqs_annot.index.isin(train_indeces)
        return sdata if copy else None


@track
def add_ranges_annot(
    sdata: SeqData, chr_delim=":", rng_delim="-", copy=False
) -> SeqData:
    """Add position annotation.
    Parameters
    ----------
    sdata : SeqData
        SeqData object.
    Returns
    -------
    SeqData
        SeqData object with position annotation.
    """
    idx = sdata.seqs_annot.index
    if chr_delim not in idx[0] or rng_delim not in idx[0]:
        raise ValueError("Invalid index format.")
    chr = [i[0] for i in idx.str.split(chr_delim)]
    rng = [i[1].split(rng_delim) for i in idx.str.split(chr_delim)]
    sdata["chr"] = chr
    sdata["start"] = [int(i[0]) for i in rng]
    sdata["end"] = [int(i[1]) for i in rng]
    return sdata if copy else None


@track
def scale_targets(
    sdata: SeqData, targets, train_key, suffix=True, copy=False
) -> SeqData:
    """Scale targets.
    Parameters
    ----------
    sdata : SeqData
        SeqData object.
    Returns
    -------
    SeqData
        SeqData object with scaled targets.
    """
    from sklearn.preprocessing import StandardScaler

    sdata = sdata.copy() if copy else sdata
    scaler = StandardScaler()
    if type(targets) is str:
        targets = [targets]
    for target in targets:
        scaler.fit(sdata[sdata[train_key] == True][target].values.reshape(-1, 1))
        # Remove _scaled to help with fragmentation?
        sdata[f"{target}_scaled" if suffix else target] = scaler.transform(
            sdata[target].values.reshape(-1, 1)
        )
    return sdata if copy else None


@track
def binarize_target_sdata(
    sdata: SeqData,
    target,
    upper_threshold=0.5,
    lower_threshold=None,
    copy=False,
    **kwargs,
) -> SeqData:
    """Binarize target.
    Parameters
    ----------
    sdata : SeqData
        SeqData object.
    Returns
    -------
    SeqData
        SeqData object with binarized target.
    """
    sdata = sdata.copy() if copy else sdata
    sdata[f"{target}_binarized"] = binarize_values(
        sdata[target], upper_threshold, lower_threshold, **kwargs
    )
    return sdata if copy else None


@track
def prepare_data(
    sdata: SeqData,
    steps=["reverse_complement", "one_hot_encode", "train_test_split"],
    # Add in an RNA flag to this
    copy=False,
) -> SeqData:
    """Prepare data.
    Parameters
    ----------
    sdata : SeqData
        SeqData object.
    Returns
    -------
    SeqData
        SeqData object with prepared data.
    """
    if not isinstance(steps, list):
        steps = [steps]

    steps = list(steps)
    pbar = tqdm(steps)
    for step in pbar:
        pbar.set_description(f"{step_name[step]} on SeqData")
        preprocessing_steps[step].__wrapped__(sdata)

    return sdata if copy else None


@track
def clamp_percentiles(
    sdata: SeqData,
    percentile: float,
    target_list: list,
    copy=False,
) -> SeqData:

    if type(target_list) is str:
        target_list = [target_list]

    for target in target_list:
        clamp_num = sdata[target].astype(float).quantile(percentile)
        sdata[target] = sdata[target].astype(float).clip(upper=clamp_num)

    return sdata if copy else None


@track
def clean_nan_data(
    sdata: SeqData,
    target_list: list,
    nan_threshold=1,
    copy=False,
):

    if type(target_list) is str:
        target_list = [target_list]

    for target in target_list:
        if (
            sdata[target].astype(float).isna().sum() / sdata[target].__len__()
            > nan_threshold
        ):
            sdata.seqs_annot = sdata.seqs_annot.drop(target, axis=1)
        else:
            sdata[target].fillna(value=0, inplace=True)

    return sdata if copy else None


preprocessing_steps = dict(
    reverse_complement=reverse_complement_data,
    one_hot_encode=one_hot_encode_data,
    train_test_split=train_test_split_data,
)

step_name = dict(
    reverse_complement="Reverse complementing",
    one_hot_encode="One hot encoding",
    train_test_split="Train/test splitting",
)
