from cloudriver_manager.models import Checkpoint, CheckpointItem
from cloudriver_manager.state import CheckpointStore


def test_checkpoint_roundtrip(tmp_path):
    store = CheckpointStore(tmp_path / "nested" / "checkpoint.json")
    state = Checkpoint(
        active={
            "c:a": CheckpointItem(
                course_version_id="c", activity_id="a", token="ABCDEFGH"
            )
        }
    )
    store.save(state)
    assert store.load().active["c:a"].token == "ABCDEFGH"
