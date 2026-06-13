"""Guard the Modal endpoint URLs the catalogue derives.

Each endpoint's public URL is ``https://<workspace>--<app>-<endpoint_name>.modal.run``.
Everything before ``.modal.run`` is a single DNS label, and DNS labels are capped
at **63 characters** — a longer one is not a valid hostname and simply fails to
resolve (this is exactly how ``nemotron-cascade-14b-thinking`` broke: its label hit
65 chars, so the host never resolved for either the health-check or the engine).

These tests keep every served model's URL within that limit so a new/renamed model
can't silently become unreachable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CATALOGUE_PATH = Path(__file__).resolve().parents[1] / "modal" / "catalogue.py"

# Max length of a single DNS label (RFC 1035). The whole subdomain before
# ".modal.run" is one label, so workspace + app + endpoint must fit inside it.
_DNS_LABEL_MAX = 63

# A representative workspace slug to size the full label against. Real workspaces
# vary, so we also bound the catalogue-controlled portion (app + endpoint) on its
# own below, leaving generous headroom for the workspace prefix.
_SAMPLE_WORKSPACE = "gharsallah-abderrahmen"  # 22 chars — a normal-length workspace

# Budget for the part the catalogue owns: "<app>-<endpoint_name>". With the
# "<workspace>--" prefix this keeps the full label under 63 for any workspace up
# to ~21 chars, with margin.
_APP_ENDPOINT_BUDGET = 40


def _catalogue():
    spec = importlib.util.spec_from_file_location("modal_catalogue_under_test", _CATALOGUE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass() resolves __module__ via sys.modules
    spec.loader.exec_module(module)
    return module


def test_every_endpoint_url_is_a_valid_dns_host():
    cat = _catalogue()
    for e in cat.entries():
        url = cat.endpoint_url(e.app, e.endpoint_name, _SAMPLE_WORKSPACE)
        label = url.removeprefix("https://").split(".modal.run", 1)[0]
        assert len(label) <= _DNS_LABEL_MAX, (
            f"{e.key}: DNS label is {len(label)} chars (> {_DNS_LABEL_MAX}); "
            f"shorten endpoint_name. label={label!r}"
        )


def test_app_plus_endpoint_stays_within_budget():
    cat = _catalogue()
    for e in cat.entries():
        owned = f"{e.app}-{e.endpoint_name}"
        assert len(owned) <= _APP_ENDPOINT_BUDGET, (
            f"{e.key}: '<app>-<endpoint_name>' is {len(owned)} chars "
            f"(> {_APP_ENDPOINT_BUDGET}); leaves too little room for the workspace "
            f"prefix in the {_DNS_LABEL_MAX}-char DNS label."
        )
