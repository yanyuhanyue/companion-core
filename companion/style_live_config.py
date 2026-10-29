"""Live style-guidance switch; off by default, so prompts and contract hashes are unchanged."""

from companion.contracts import StrictModel


class StyleLiveConfig(StrictModel):
    enabled: bool = False
