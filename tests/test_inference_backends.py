"""Tests for the unified inference backend registry (Modal + Hugging Face).

The registry is the façade the router, the config loader, and the Lab UI all read:
backend-qualified keys, a uniform entries/binding view across backends, bare keys
defaulting to Modal (backward compatibility), and per-backend credential gates.
"""

from __future__ import annotations

import pytest

from src.models import hf_catalogue, inference, modal_catalogue


def test_modal_and_hf_are_registered():
    keys = {b.key for b in inference.backends()}
    assert {"modal", "hf"} <= keys
    assert inference.DEFAULT_BACKEND == "modal"


def test_split_key_defaults_bare_to_modal():
    assert inference.split_key("gemma-4-12b") == ("modal", "gemma-4-12b")
    assert inference.split_key("modal:gemma-4-12b") == ("modal", "gemma-4-12b")
    assert inference.split_key("hf:org/model") == ("hf", "org/model")
    # An unknown prefix is treated as part of a bare Modal key, not a backend.
    assert inference.split_key("weird:thing") == ("modal", "weird:thing")


def test_qualify_keeps_modal_bare_and_prefixes_others():
    assert inference.qualify("modal", "gemma-4-12b") == "gemma-4-12b"
    assert inference.qualify("hf", "org/model") == "hf:org/model"


def test_entries_are_tagged_and_qualified():
    modal_keys = {e["key"] for e in inference.entries("modal")}
    hf_keys = {e["key"] for e in inference.entries("hf")}
    # Modal entries keep bare keys; HF entries are qualified; the two are disjoint.
    assert modal_keys == {e["key"] for e in modal_catalogue.entries()}
    assert all(k.startswith("hf:") for k in hf_keys)
    assert modal_keys.isdisjoint(hf_keys)
    # The unqualified call returns both backends' models, each tagged with its backend.
    everything = inference.entries()
    assert {e["backend"] for e in everything} == {"modal", "hf"}
    assert len(everything) == len(modal_keys) + len(hf_keys)


def test_entry_by_key_round_trips_both_backends():
    modal_key = modal_catalogue.entries()[0]["key"]
    hf_key = inference.qualify("hf", hf_catalogue.entries()[0]["key"])
    assert inference.entry_by_key(modal_key)["backend"] == "modal"
    assert inference.entry_by_key(hf_key)["backend"] == "hf"
    assert inference.entry_by_key("nope:nothing") is None


def test_binding_dispatches_to_the_right_backend():
    hf_key = inference.qualify("hf", hf_catalogue.default_key_for_profile("tiny"))
    binding = inference.binding_for(hf_key, env={"HF_TOKEN": "tok"})
    assert binding["base_url"] == hf_catalogue.DEFAULT_BASE_URL
    assert binding["api_key"] == "tok"

    modal_key = modal_catalogue.default_key_for_profile("balanced")
    modal_binding = inference.binding_for(modal_key, env={"MODAL_WORKSPACE": "ws", "MODAL_LLM_KEY": "EMPTY"})
    assert "modal.run" in modal_binding["base_url"]


def test_default_key_for_profile_is_backend_scoped():
    # HF currently tags only the tiny tier (its single live chat model); Modal tags
    # every tier. The point here is that keys are namespaced per backend.
    hf_default = inference.default_key_for_profile("tiny", "hf")
    assert hf_default is not None and hf_default.startswith("hf:")
    modal_default = inference.default_key_for_profile("strong", "modal")
    assert modal_default is not None and not modal_default.startswith("hf:")


def test_backend_available_and_configured_backends():
    assert inference.backend_available("modal", env={"MODAL_WORKSPACE": "ws"}) is True
    assert inference.backend_available("hf", env={"HF_TOKEN": "x"}) is True
    assert inference.backend_available("modal", env={}) is False
    assert inference.backend_available("hf", env={}) is False
    assert inference.backend_available("nope", env={"HF_TOKEN": "x"}) is False

    both = inference.configured_backends(env={"MODAL_WORKSPACE": "ws", "HF_TOKEN": "x"})
    assert both == ["modal", "hf"]  # display order: Modal first
    assert inference.configured_backends(env={}) == []


def test_binding_unknown_backend_raises():
    # entry_by_key tolerates unknown keys, but binding_for surfaces a config error.
    with pytest.raises(KeyError):
        inference.binding_for("hf:does/not-exist", env={"HF_TOKEN": "x"})
