from litrag_parser.facets import normalise, role_of


def test_normalise_strips_numbering_and_punctuation():
    assert normalise("2.1. Materials and Methods:") == "materials and methods"
    assert normalise("III. RESULTS") == "results"
    assert normalise("  Discussion  ") == "discussion"


def test_roles_from_the_vocabulary():
    assert role_of("Materials and Methods") == "methods"
    assert role_of("2. Experimental") == "methods"
    assert role_of("Experimental Results") == "results"
    assert role_of("Results and Discussion") == "results-discussion"
    assert role_of("Concluding remarks") == "discussion"
    assert role_of("5. Conclusions") == "discussion"
    assert role_of("References") == "references"
    assert role_of("Author Contributions") == "back"
    assert role_of("Abstract") == "abstract"


def test_unmatched_headings_fall_to_other_not_a_guess():
    assert role_of("Cell lines and cultures") == "other"
    assert role_of("2.8. Computational Modeling") == "other"
    assert role_of("") == "other"
