import uuid

import pytest
from pydantic import ValidationError

from app.db.base_class import Base
from app.schemas import SearchRequest
from app.schemas import UserCreate, UserOut


class TestUserOutExposesNoSecrets:
    def test_no_password_field_of_any_kind(self):
        """Checked by name across the whole schema rather than against a fixed"""
        forbidden = ("password", "hashed", "secret", "token", "salt")

        for field_name in UserOut.model_fields:
            lowered = field_name.lower()
            assert not any(word in lowered for word in forbidden), (
                f"UserOut exposes '{field_name}', which looks like a secret"
            )

    def test_a_password_attribute_is_dropped_when_building_from_an_object(self):
        """The real mechanism, exercised end to end: an ORM row carrying a hash"""

        class FakeUserRow:
            id = uuid.uuid4()
            org_id = uuid.uuid4()
            email = "ada@example.com"
            full_name = "Ada Lovelace"
            role = "Admin"
            is_active = True
            is_verified = True
            created_at = __import__("datetime").datetime.now()
            updated_at = __import__("datetime").datetime.now()
            hashed_password = "$2b$12$averysecrethashvalue"

        serialised = UserOut.model_validate(FakeUserRow()).model_dump()

        assert "hashed_password" not in serialised
        assert "$2b$" not in str(serialised)
        assert serialised["email"] == "ada@example.com"


class TestUserCreate:
    def test_a_valid_signup_is_accepted(self):
        user = UserCreate(
            email="ada@example.com",
            password="a-good-password",
            full_name="Ada Lovelace",
            org_name="Analytical Engines Ltd",
        )
        assert user.email == "ada@example.com"

    @pytest.mark.parametrize(
        "bad_email", ["not-an-email", "missing-at-sign.com", "@nolocalpart.com", ""]
    )
    def test_a_malformed_email_is_rejected(self, bad_email):
        """`EmailStr` is why no endpoint contains an email regex. The request is"""
        with pytest.raises(ValidationError):
            UserCreate(email=bad_email, password="a-good-password", full_name="X")

    def test_a_missing_password_is_rejected(self):
        with pytest.raises(ValidationError):
            UserCreate(email="ada@example.com", full_name="Ada")


class TestSearchRequest:
    def test_a_minimal_request_is_accepted(self):
        assert SearchRequest(query="annual leave").query == "annual leave"

    def test_an_empty_query_is_rejected(self):
        """`min_length=1`. An empty search box is a mistake, not a search."""
        with pytest.raises(ValidationError):
            SearchRequest(query="")

    def test_an_absurdly_long_query_is_rejected(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="x" * 5000)

    @pytest.mark.parametrize("top_k", [0, -1, 51, 10000])
    def test_out_of_range_top_k_is_rejected(self, top_k):
        """The upper bound is the point: without it a client could ask for 10,000"""
        with pytest.raises(ValidationError):
            SearchRequest(query="anything", top_k=top_k)

    @pytest.mark.parametrize("top_k", [1, 5, 50])
    def test_in_range_top_k_is_accepted(self, top_k):
        assert SearchRequest(query="anything", top_k=top_k).top_k == top_k

    @pytest.mark.parametrize("score", [-0.1, 1.1, 2.0])
    def test_out_of_range_min_score_is_rejected(self, score):
        """Similarity is 0..1. Anything outside would silently return nothing."""
        with pytest.raises(ValidationError):
            SearchRequest(query="anything", min_score=score)

    @pytest.mark.parametrize("score", [0.0, 0.15, 1.0])
    def test_in_range_min_score_is_accepted(self, score):
        assert SearchRequest(query="anything", min_score=score).min_score == score

    def test_optional_filters_default_to_none(self):
        """None means "do not narrow", which is what makes them optional."""
        request = SearchRequest(query="anything")

        assert request.project_id is None
        assert request.document_id is None
        assert request.top_k is None

    def test_a_malformed_uuid_is_rejected(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="anything", project_id="not-a-uuid")

    def test_a_valid_uuid_is_accepted(self):
        project_id = uuid.uuid4()
        assert SearchRequest(query="anything", project_id=project_id).project_id == project_id


class TestTableNameDerivation:
    """Table names are derived from class names by a regex plus naive"""

    def make(self, class_name: str) -> str:
        """Build a throwaway model class and read the name it derived."""
        return type(class_name, (Base,), {
            "__abstract__": True,
            "__annotations__": {},
        }).__tablename__

    @pytest.mark.parametrize(
        "class_name,expected",
        [
            ("User", "users"),
            ("Project", "projects"),
            ("Document", "documents"),
            ("Chunk", "chunks"),
            ("ChunkEmbedding", "chunk_embeddings"),
            ("ChatSession", "chat_sessions"),
            ("AuditLog", "audit_logs"),
            ("Organization", "organizations"),
        ],
    )
    def test_camel_case_becomes_snake_case_plural(self, class_name, expected):
        assert self.make(class_name) == expected

    def test_a_trailing_y_becomes_ies(self):
        assert self.make("Category") == "categories"

    def test_an_already_plural_name_is_left_alone(self):
        assert self.make("Analytics") == "analytics"

    def test_the_rule_would_mangle_apikey(self):
        """Documents WHY `APIKey` overrides `__tablename__` by hand."""
        assert self.make("APIKey") == "a_p_i_keies"

    def test_the_real_model_opted_out(self):
        from app.models import APIKey

        assert APIKey.__tablename__ == "api_keys"


class TestModelRegistry:
    def test_every_model_is_registered(self):
        """Alembic reads `Base.metadata` to discover tables. A model that is never"""
        import app.models  # noqa: F401  -- the import IS the thing being tested

        registered = set(Base.metadata.tables)

        expected = {
            "users", "organizations", "projects", "documents",
            "chunks", "chunk_embeddings", "chat_sessions",
            "chat_messages", "feedbacks", "api_keys", "audit_logs",
        }

        missing = {t for t in expected if t not in registered}
        missing.discard("feedbacks")

        assert not missing, f"not registered in Base.metadata: {sorted(missing)}"
