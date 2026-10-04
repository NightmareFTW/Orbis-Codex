"""Read-only vision: find the game window, capture its pixels, (later) read them with OCR.

Hard rule (CLAUDE.md, SPEC D10/D36): only screen pixels and public window metadata. Nothing here opens a handle to the
game process, reads its memory, captures its network traffic or sends it input. `tests/test_vision.py` enforces this.
"""
