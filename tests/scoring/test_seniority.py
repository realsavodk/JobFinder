from job_scout.scoring.seniority import estimate_min_years


def test_senior_title_implies_five_years():
    assert estimate_min_years("Senior Backend Engineer", "") == 5


def test_staff_and_principal_imply_seven_years():
    assert estimate_min_years("Staff Engineer", "") == 7
    assert estimate_min_years("Principal Engineer", "") == 7


def test_director_outranks_senior_when_both_present_in_title():
    assert estimate_min_years("Senior Director of Engineering", "") == 8


def test_junior_and_new_grad_titles_imply_zero():
    assert estimate_min_years("Junior Software Engineer", "") == 0
    assert estimate_min_years("Software Engineer, New Grad", "") == 0


def test_lead_does_not_false_positive_on_leadership():
    # "lead" must match as a whole word, not as a substring of "leadership"
    assert estimate_min_years("Leadership Development Program Associate", "") == 1


def test_plain_title_falls_back_to_description_text():
    assert (
        estimate_min_years("Software Engineer", "requires 5+ years of experience") == 5
    )
    assert (
        estimate_min_years("Software Engineer", "3-5 years of experience required") == 3
    )
    assert estimate_min_years("Software Engineer", "at least 4 years in backend") == 4


def test_no_signal_at_all_returns_none():
    assert estimate_min_years("Software Engineer", "build cool things with us") is None


def test_text_extraction_is_conservative_and_takes_the_highest_number():
    text = "2+ years in SQL and 5+ years in distributed systems required"
    assert estimate_min_years("Software Engineer", text) == 5
