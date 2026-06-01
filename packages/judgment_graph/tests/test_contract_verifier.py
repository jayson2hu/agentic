from judgment_graph.scripts.verify_contracts import (
    verify_feature_markers,
    verify_no_l3_imports,
    verify_no_real_llm_clients,
    verify_owned_tables,
)


def test_contract_verifier_passes_current_tree() -> None:
    verify_owned_tables()
    verify_no_l3_imports()
    verify_no_real_llm_clients()
    verify_feature_markers()
