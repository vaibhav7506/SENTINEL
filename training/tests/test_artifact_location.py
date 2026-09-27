import pytest

from training.artifacts import ROOT, resolve_dataset


def test_portable_identity_stays_inside_trusted_root():
    name = "1bcd8acdc1fab6f9"
    for reference in (
        rf"C:\old\workspace\artifacts\datasets\{name}",
        f"/old/workspace/artifacts/datasets/{name}",
        f"artifacts/datasets/{name}",
    ):
        assert resolve_dataset(reference) == (ROOT / "artifacts/datasets" / name).resolve()


@pytest.mark.parametrize(
    "reference", ["/tmp/untrusted", "artifacts/datasets/..", "http://evil/data"]
)
def test_invalid_dataset_identity_is_rejected(reference):
    with pytest.raises(ValueError):
        resolve_dataset(reference)
