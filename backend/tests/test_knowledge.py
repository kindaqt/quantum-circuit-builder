"""Tests for the curated knowledge base + retrieval (knowledge.py, RAG phase 1).

These guard two contracts: (1) every whitelisted gate in core.ALLOWED has a note,
so adding a gate without documenting it fails here rather than shipping silently;
(2) retrieve()/reference_block() pull the right notes for a circuit, always include
the foundational concepts, dedupe, and stay within the size bounds. Pure-data tests:
no DOM, no network, no LLM.
"""
import core
import knowledge
from core import CircuitSpec, Gate


def spec(num_qubits=2, gates=()):
    return CircuitSpec(
        num_qubits=num_qubits,
        gates=[g if isinstance(g, Gate) else Gate(**g) for g in gates],
    )


def titles(chunks):
    return [t for t, _ in chunks]


# --------------------------------------------------------------------------- #
# Corpus integrity: the note set must track the gate whitelist.
# --------------------------------------------------------------------------- #
def test_every_whitelisted_gate_has_a_note():
    missing = [name for name in core.ALLOWED if name not in knowledge.GATE_NOTES]
    assert missing == [], f"gates in ALLOWED without a knowledge note: {missing}"


def test_every_gate_concept_trigger_points_at_a_real_concept():
    for name, concepts in knowledge._GATE_CONCEPTS.items():
        assert name in knowledge.GATE_NOTES, f"{name} triggers concepts but has no note"
        for c in concepts:
            assert c in knowledge.CONCEPT_NOTES, f"{name} triggers unknown concept {c}"


def test_always_on_concepts_exist():
    for c in knowledge._ALWAYS_CONCEPTS:
        assert c in knowledge.CONCEPT_NOTES


# --------------------------------------------------------------------------- #
# retrieve(): happy paths
# --------------------------------------------------------------------------- #
def test_retrieve_includes_the_note_for_each_gate_present():
    chunks = knowledge.retrieve(spec(2, [{"name": "h", "qubits": [0]},
                                         {"name": "cx", "qubits": [0, 1]}]))
    ts = titles(chunks)
    assert any("Hadamard" in t for t in ts)
    assert any("CX" in t for t in ts)


def test_retrieve_always_includes_endianness_and_measurement():
    # Even an empty circuit gets the two foundational concept notes.
    chunks = knowledge.retrieve(spec(1, []))
    ts = titles(chunks)
    assert any("little-endian" in t for t in ts)
    assert any("Measurement" in t for t in ts)


def test_h_triggers_superposition_and_cx_triggers_entanglement():
    chunks = knowledge.retrieve(spec(2, [{"name": "h", "qubits": [0]},
                                         {"name": "cx", "qubits": [0, 1]}]))
    ts = titles(chunks)
    assert any("Superposition" in t for t in ts)
    assert any("Entanglement" in t for t in ts)


def test_phase_gate_triggers_interference():
    chunks = knowledge.retrieve(spec(1, [{"name": "t", "qubits": [0]}]))
    assert any("interference" in t.lower() for t in titles(chunks))


def test_gate_notes_are_deduped_when_a_gate_repeats():
    chunks = knowledge.retrieve(spec(1, [{"name": "h", "qubits": [0]},
                                         {"name": "h", "qubits": [0]}]))
    hadamards = [t for t in titles(chunks) if "Hadamard" in t]
    assert len(hadamards) == 1


def test_gate_notes_come_before_concept_notes():
    chunks = knowledge.retrieve(spec(1, [{"name": "h", "qubits": [0]}]))
    ts = titles(chunks)
    assert ts.index("Hadamard (H)") < ts.index("Qubit ordering (little-endian)")


# --------------------------------------------------------------------------- #
# retrieve(): bounds + sad paths
# --------------------------------------------------------------------------- #
def test_retrieve_respects_the_chunk_cap(monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_CHUNKS", 3)
    chunks = knowledge.retrieve(spec(3, [{"name": "h", "qubits": [0]},
                                         {"name": "cx", "qubits": [0, 1]},
                                         {"name": "ccx", "qubits": [0, 1, 2]}]))
    assert len(chunks) == 3


def test_retrieve_respects_the_char_cap_but_keeps_at_least_one(monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_CHARS", 1)  # absurdly small
    chunks = knowledge.retrieve(spec(1, [{"name": "h", "qubits": [0]}]))
    assert len(chunks) == 1  # never blanks the block entirely


def test_unknown_gate_name_is_skipped_not_fatal():
    # validate() rejects unknown names upstream, but retrieve must not crash if asked.
    chunks = knowledge.retrieve(spec(1, [Gate(name="nope", qubits=[0])]))
    # No gate note for "nope", but the always-on concepts are still present.
    assert any("little-endian" in t for t in titles(chunks))


# --------------------------------------------------------------------------- #
# reference_block(): rendering
# --------------------------------------------------------------------------- #
def test_reference_block_renders_titles_as_bullets():
    block = knowledge.reference_block(spec(2, [{"name": "h", "qubits": [0]},
                                               {"name": "cx", "qubits": [0, 1]}]))
    assert block.startswith("Reference notes")
    assert "- Hadamard (H):" in block
    assert "- CX (CNOT):" in block


# --------------------------------------------------------------------------- #
# Topic corpus integrity (RAG phase 2 — corpus). The TF-IDF retriever lands next;
# these guard the corpus and its eval set so they stay well-formed and consistent.
# --------------------------------------------------------------------------- #
def test_topic_notes_are_well_formed():
    assert knowledge.TOPIC_NOTES, "topic corpus must not be empty"
    for note_id, note in knowledge.TOPIC_NOTES.items():
        assert note.id == note_id, f"{note_id}: key must match note.id"
        assert note.title.strip(), f"{note_id}: empty title"
        assert note.text.strip(), f"{note_id}: empty text"
        assert note.category in knowledge.TOPIC_CATEGORIES, \
            f"{note_id}: unknown category {note.category!r}"
        assert isinstance(note.keywords, tuple), f"{note_id}: keywords must be a tuple"


def test_topic_note_titles_are_unique():
    titles = [n.title for n in knowledge.TOPIC_NOTES.values()]
    dupes = {t for t in titles if titles.count(t) > 1}
    assert dupes == set(), f"duplicate topic-note titles: {dupes}"


def test_every_category_has_at_least_one_note():
    present = {n.category for n in knowledge.TOPIC_NOTES.values()}
    missing = [c for c in knowledge.TOPIC_CATEGORIES if c not in present]
    assert missing == [], f"categories with no notes: {missing}"


def test_keywords_have_no_duplicates_within_a_note():
    for note_id, note in knowledge.TOPIC_NOTES.items():
        assert len(note.keywords) == len(set(note.keywords)), \
            f"{note_id}: duplicate keywords"


def test_eval_cases_point_at_real_notes():
    # Guard so an eval case can't reference a renamed/deleted note unnoticed.
    assert knowledge.EVAL_CASES, "eval set must not be empty"
    for query, expected_ids in knowledge.EVAL_CASES:
        assert query.strip(), "eval query must be non-empty"
        assert expected_ids, f"{query!r}: must expect at least one note"
        for nid in expected_ids:
            assert nid in knowledge.TOPIC_NOTES, \
                f"eval case {query!r} expects unknown note id {nid!r}"


# --------------------------------------------------------------------------- #
# retrieve_topics(): TF-IDF free-text retrieval over the topic corpus.
# --------------------------------------------------------------------------- #
def ids_of(hits):
    return [h.note.id for h in hits]


def test_retrieve_topics_finds_the_obvious_note():
    hits = knowledge.retrieve_topics("how does grover's algorithm work")
    assert hits, "a clear on-topic query should retrieve something"
    assert hits[0].note.id == "grover"  # the best match is rank 1


def test_keyword_alias_matches_even_without_the_title_words():
    # "epr pair" never appears in the bell_state title/text — only as a curated alias.
    hits = knowledge.retrieve_topics("explain an epr pair")
    assert "bell_state" in ids_of(hits)


def test_retrieve_topics_abstains_on_empty_and_blank():
    assert knowledge.retrieve_topics("") == []
    assert knowledge.retrieve_topics("   ") == []


def test_retrieve_topics_abstains_on_off_topic_query():
    # Nothing in the corpus is about lunch; abstaining beats a misleading note.
    assert knowledge.retrieve_topics("what is the best pizza topping") == []


def test_retrieve_topics_respects_k():
    hits = knowledge.retrieve_topics("entanglement bell ghz teleportation", k=2)
    assert len(hits) <= 2


def test_top_hit_is_the_strongest_and_every_hit_carries_a_reason():
    # The rank-1 hit is always the highest-scoring note (MMR applies no diversity
    # penalty to the first pick); later picks may reorder slightly for diversity, so
    # we don't assert strict monotonicity across the whole list.
    hits = knowledge.retrieve_topics("what is quantum error correction")
    assert hits[0].score == max(h.score for h in hits)
    assert all(h.why for h in hits)  # every hit explains why it matched


def test_topic_reference_block_renders_or_abstains():
    block = knowledge.topic_reference_block("what is the surface code")
    assert block.startswith("Reference notes")
    assert "Surface code" in block
    assert knowledge.topic_reference_block("best pizza topping") == ""


def test_retriever_recall_on_eval_set():
    """The retriever must surface the expected note within the top-k for the whole
    eval set. This is the regression guard: tightening a knob or editing the corpus
    can't quietly drop retrieval quality below the bar (currently a perfect sweep)."""
    misses = []
    for query, expected_ids in knowledge.EVAL_CASES:
        got = set(ids_of(knowledge.retrieve_topics(query, k=knowledge.TOPIC_TOP_K)))
        if not (got & expected_ids):
            misses.append((query, expected_ids, got))
    assert misses == [], f"recall@{knowledge.TOPIC_TOP_K} misses: {misses}"


def test_retriever_top1_recall_is_high():
    # A softer bar than the strict eval sweep above: most queries should rank the
    # expected note first. Guards against a regression that keeps recall@k but
    # scrambles ordering. Allow a small slack so one tie-break can't fail the build.
    top1 = sum(
        1 for query, expected_ids in knowledge.EVAL_CASES
        if (h := knowledge.retrieve_topics(query, k=1)) and h[0].note.id in expected_ids
    )
    assert top1 >= len(knowledge.EVAL_CASES) - 2


def test_singularization_matches_plurals_and_possessives():
    # "grovers" (no apostrophe) and "grover's" should both reach the grover note.
    for q in ("how does grovers algorithm work", "explain grover's algorithm"):
        assert "grover" in ids_of(knowledge.retrieve_topics(q))


# --------------------------------------------------------------------------- #
# combined_reference_block(): multi-corpus merge of gate notes + topic notes.
# --------------------------------------------------------------------------- #
def test_combined_block_merges_circuit_notes_and_topic_notes():
    # A circuit with H+CX plus a general question: the block carries both the gate
    # notes (from the circuit) and the topic note (from the question).
    block = knowledge.combined_reference_block(
        spec(2, [{"name": "h", "qubits": [0]}, {"name": "cx", "qubits": [0, 1]}]),
        "how does grover's algorithm work")
    assert "Hadamard (H)" in block      # circuit-driven gate note
    assert "Grover's search" in block   # question-driven topic note


def test_combined_block_without_a_question_is_just_circuit_notes():
    block = knowledge.combined_reference_block(
        spec(1, [{"name": "h", "qubits": [0]}]), None)
    assert "Hadamard (H)" in block
    assert "Grover" not in block        # no question -> no topic retrieval


def test_combined_block_grounds_a_general_question_on_an_empty_circuit():
    # Tutor mode: even with nothing on the canvas, a general question pulls its note.
    block = knowledge.combined_reference_block(spec(1, []), "what is decoherence")
    assert "Decoherence" in block
