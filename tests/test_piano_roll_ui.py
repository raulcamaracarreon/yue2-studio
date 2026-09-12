import base64
import re

from tools.piano_roll_ui import build_piano_roll_editor


ABC = '''X:1
T:
M:4/4
L:1/16
Q:1/4=90
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:Dm
% demo
V: Vocal
"Dm"A4A4c4A4|"Gm"G8z8|
V: Ins
D4F4A4F4|G4B4d4B4|
'''


def _iframe_html(component_html: str) -> str:
    match = re.search(r'src="data:text/html;base64,([^"]+)"', component_html)
    assert match, "se esperaba un iframe data:base64"
    return base64.b64decode(match.group(1)).decode("utf-8")


def test_editor_renders_both_tracks_and_apply_bridge():
    html = _iframe_html(build_piano_roll_editor(ABC, "test-workspace", interactive=True))
    assert "Vocal" in html
    assert "Ins" in html
    assert "Aplicar al ABC" in html
    assert "yue2-pianoroll-apply" in html
    assert "test-workspace" in html


def test_read_only_editor_hides_apply_action():
    html = _iframe_html(build_piano_roll_editor(ABC, "readonly", interactive=False))
    assert "const INTERACTIVE = false" in html


def test_empty_score_returns_placeholder():
    html = build_piano_roll_editor("", "empty", interactive=True)
    assert "piano roll aparecerá" in html.lower()
