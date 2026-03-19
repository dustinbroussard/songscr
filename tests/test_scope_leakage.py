import pytest
from songscr.core import parse_song

def test_local_tags_do_not_leak_into_global_meta():
    text = """
[Tempo: 120]
[Section 1]
[Track: Chords]
[Tempo: 80]
| C | G |
    """
    song = parse_song(text)
    
    # Global tempo should remain 120, not be overwritten by 80
    assert song.meta.get("tempo") == "120"
    
    # Verify the track tag is actually recorded
    section = song.sections[0]
    track = section.tracks["Chords"]
    
    local_tempos = [t.value for t in track.tags if t.name.lower() == "tempo"]
    assert "80" in local_tempos

def test_local_tags_are_isolated_for_different_keys():
    text = """
[Key: C]
[Section 1]
[Track: Chords]
[Key: F]
| F | F |
    """
    song = parse_song(text)
    assert song.meta.get("key") == "C"
