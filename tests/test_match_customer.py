"""app/services/match_customer.py's fuzzy customer resolution, and
normalize_customer_name (app/services/normalization.py) - the account-type/
legal-suffix noise stripping and st/saint canonicalization added after a
real "Saint George" ambiguity: the live catalogue has 20+ distinct
customers containing "saint george"/"st george" (a hospital, a
restaurant, several churches, private individuals), and roughly a quarter
of every customer in the table is prefixed "Mem."/"Emp." (Membre/Employe -
an individual account under a company), neither of which a salesman would
ever actually say out loud.
"""
from app.services.match_customer import match_customer
from app.services.normalization import normalize_customer_name
from app.schemas.enums import MatchStatus


def _customer(db_session, nb, name):
    from app.models import Customer
    c = Customer(customer_number=nb, customer_name=name, salesman_id=None)
    db_session.add(c)
    db_session.flush()
    return c


class TestNormalizeCustomerName:
    def test_saint_and_st_canonicalize_to_the_same_token(self):
        assert (normalize_customer_name("Saint George")
               == normalize_customer_name("St George")
               == normalize_customer_name("St. George")
               == "st george")

    def test_mem_prefix_stripped(self):
        assert normalize_customer_name("Mem. Mirella Noun") == "mirella noun"

    def test_emp_prefix_stripped(self):
        assert normalize_customer_name("Emp. Jawad Bassil") == "jawad bassil"

    def test_sal_suffix_stripped_without_periods(self):
        assert (normalize_customer_name("AUTOMOTIVE FRANCHISING sal")
               == "automotive franchising")

    def test_sal_suffix_stripped_with_periods(self):
        # "S.A.L" survives normalize_text()'s punctuation stripping as
        # three separate single-letter tokens ("s a l") - must still
        # collapse to the same stripped result as the unpunctuated form.
        assert normalize_customer_name("THE TALKIES S.A.L") == "the talkies"

    def test_sarl_suffix_stripped(self):
        assert (normalize_customer_name("INTECH GENERAL TRADING sarl")
               == "intech general trading")

    def test_noise_prefix_that_is_also_a_real_word_is_not_over_stripped(self):
        # "mem"/"emp"/"pat"/"sal"/"sarl" only strip as a leading/trailing
        # whole token, never mid-name - a name that just happens to start
        # or end with one of these as part of a real word must survive
        # intact.
        assert normalize_customer_name("Salma Khoury") == "salma khoury"
        assert normalize_customer_name("Memo Traders") == "memo traders"


class TestMatchCustomerRealWorldAmbiguity:
    """The exact scenario that motivated this fix: multiple real customers
    share "saint george"/"st george" in their name, and the account-type
    prefixes were previously pure noise degrading the fuzzy score against
    what a salesman actually says."""

    def test_plain_name_resolves_despite_st_saint_spelling_difference(
            self, db_session):
        _customer(db_session, "C1", "ST. GEORGE")
        _customer(db_session, "C2", "HOSPITAL ST GEORGE")
        _customer(db_session, "C3", "SAINT GEORGE RESTAURANT")

        m = match_customer(db_session, "Saint George")

        assert m.status == MatchStatus.matched
        assert m.customer_number == "C1"

    def test_qualified_name_disambiguates_the_right_one(self, db_session):
        _customer(db_session, "C1", "ST. GEORGE")
        _customer(db_session, "C2", "HOSPITAL ST GEORGE")
        _customer(db_session, "C3", "SAINT GEORGE RESTAURANT")

        m = match_customer(db_session, "Hospital Saint George")

        assert m.status == MatchStatus.matched
        assert m.customer_number == "C2"

    def test_spoken_name_matches_despite_mem_prefix(self, db_session):
        _customer(db_session, "C1", "Mem. Mirella Noun")

        m = match_customer(db_session, "Mirella Noun")

        assert m.status == MatchStatus.matched
        assert m.customer_number == "C1"

    def test_spoken_name_matches_despite_legal_suffix(self, db_session):
        _customer(db_session, "C1", "AUTOMOTIVE FRANCHISING sal")

        m = match_customer(db_session, "Automotive Franchising")

        assert m.status == MatchStatus.matched
        assert m.customer_number == "C1"

    def test_genuinely_distinct_mem_customers_still_stay_ambiguous(
            self, db_session):
        # The noise-stripping must not paper over a real ambiguity - two
        # different people with the same actual name are still ambiguous
        # once "Mem." stops being a distinguishing prefix.
        _customer(db_session, "C1", "Mem. Georges Khoury")
        _customer(db_session, "C2", "Emp. Georges Khoury")

        m = match_customer(db_session, "Georges Khoury")

        assert m.status == MatchStatus.ambiguous
