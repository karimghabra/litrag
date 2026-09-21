"""The Symbol-font errors of older Wiley PDFs, undone in the contexts that make it safe."""

from litrag_parser.glyphs import glyph_residue, repair_glyphs


def test_equals_plus_times_and_the_exponent_minus():
    assert repair_glyphs("The digestion solution was a mixture (pH ¼ 7.4), which contained 0.1 M Tris.") == "The digestion solution was a mixture (pH = 7.4), which contained 0.1 M Tris."
    assert repair_glyphs("Threads ( n ¼ 13-15) were analyzed; current density ¼ current/height.") == "Threads ( n = 13-15) were analyzed; current density = current/height."
    assert repair_glyphs("the mix (culture medium þ 10% alamar blue) was added; CD4 þ and CD8 þ lymphocytes") == "the mix (culture medium + 10% alamar blue) was added; CD4+ and CD8+ lymphocytes"
    assert repair_glyphs("A cell seeding density of 1 - 10 6 cells/mL-gel was used") == "A cell seeding density of 1 × 10^6 cells/mL-gel was used"
    assert repair_glyphs("powder standard ( q ¼ 0.107623 Å \x00 1 ).") == "powder standard ( q = 0.107623 Å^-1 )."


def test_what_is_not_a_symbol_error_is_left_alone():
    assert repair_glyphs("a quarter, written ¼, of the dose") == "a quarter, written ¼, of the dose"  # no word on the left of the sign
    assert repair_glyphs("Þorvaldur wrote it") == "Þorvaldur wrote it"  # an Icelandic name keeps its thorn
    assert repair_glyphs("samples 1 - 10 were pooled") == "samples 1 - 10 were pooled"  # a range, not an exponent
    assert repair_glyphs("nothing to fix here.") == "nothing to fix here."
    assert glyph_residue("pH ¼ 7.4 and þ more") == 2 and glyph_residue("clean") == 0


def test_a_ligature_the_font_drew_with_a_glyph_of_its_own_is_undone_where_it_makes_a_known_word():
    from litrag_parser.glyphs import repair_glyphs

    # Hindawi's STIX fonts: the "fi" read as its "f" alone
    assert repair_glyphs("were identifed and classifed; the beneft was signifcant") == "were identified and classified; the benefit was significant"
    assert repair_glyphs("prefltered samples were confrmed") == "prefiltered samples were confirmed"
    # RSC: a private-use glyph for the ligature, inside a word or at its start
    assert repair_glyphs("the identi\ue103cation of bene\ue103ts and \ue103nal results") == "the identification of benefits and final results"
    # the presentation forms, always
    assert repair_glyphs("a \ufb01ne \ufb02ow") == "a fine flow"
    # Wiley's Advanced journals: the ligature kept with the left fragment
    assert repair_glyphs("the specifi c capacity is briefl y summarized") == "the specific capacity is briefly summarized"
    assert repair_glyphs("the staff took the cutoff off site") == "the staff took the cutoff off site"
    # words that are words stay as they are
    assert repair_glyphs("we fed the left side after soft deformation of the shelf") == "we fed the left side after soft deformation of the shelf"
