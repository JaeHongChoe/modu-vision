"""A recorded seed identifies assignments independently of directory order."""
import itertools

import pytest

from backend.engine.dataset_loaders import split_dataset


@pytest.mark.parametrize("labels", [("OK", "NG"), ("OK", "OK", "NG", "NG", "OK", "NG")])
@pytest.mark.parametrize("identity", ["image_path", "id"])
def test_split_seed_is_stable_across_directory_enumerations(labels, identity):
    items = [{identity: f"/owned/{label}/{index}.png", "label": label}
             for index, label in enumerate(labels)]
    def assignments(rows):
        result = split_dataset(rows, train_ratio=.5, val_ratio=.5, seed=17)
        return {row[identity]: partition
                for partition, images in result.items() for row in images}
    expected = assignments(items)
    for enumeration in itertools.permutations(items):
        assert assignments(list(enumeration)) == expected
    assert set(expected) == {row[identity] for row in items}
    assert set(expected.values()) == {"train", "val"}
