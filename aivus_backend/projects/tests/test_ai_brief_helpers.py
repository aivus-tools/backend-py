"""Unit tests for AI brief helper functions (no LLM calls)."""

from aivus_backend.projects.ai_brief_v2 import filter_scope_photo
from aivus_backend.projects.ai_brief_v2 import strip_wrong_language_patches


class TestStripWrongLanguagePatches:
    def test_drops_cyrillic_when_english(self):
        patches = {
            "project_header": "<h2>Project Header</h2><p>English content</p>",
            "budget_timeline": "<h2>Бюджет</h2><p>500 рублей</p>",
        }
        result = strip_wrong_language_patches(patches, "en")
        assert "project_header" in result
        assert "budget_timeline" not in result

    def test_drops_latin_when_russian(self):
        patches = {
            "project_header": "<h2>Заголовок</h2><p>Контент</p>",
            "budget_timeline": "<h2>Budget Timeline</h2><p>Standard fields</p>",
        }
        result = strip_wrong_language_patches(patches, "ru")
        assert "project_header" in result
        assert "budget_timeline" not in result

    def test_allows_mixed_with_brand_names_for_russian(self):
        patches = {
            "deliverables": "<h2>Доставки</h2><p>Instagram, YouTube</p>",
        }
        result = strip_wrong_language_patches(patches, "ru")
        assert "deliverables" in result

    def test_allows_mixed_with_terms_for_english(self):
        patches = {
            "creative_direction": "<h2>Creative</h2><p>Casting in NYC</p>",
        }
        result = strip_wrong_language_patches(patches, "en")
        assert "creative_direction" in result

    def test_passthrough_for_unknown_language(self):
        patches = {"project_header": "<h2>Заголовок</h2>"}
        result = strip_wrong_language_patches(patches, "")
        assert result == patches

    def test_keeps_empty_patches(self):
        patches = {"project_header": ""}
        result = strip_wrong_language_patches(patches, "en")
        assert result == patches

    def test_multiple_sections_one_dropped(self):
        patches = {
            "project_header": "<p>English Title</p>",
            "budget_timeline": "<p>Бюджет в рублях</p>",
            "scope_video": "<p>Production details in English</p>",
        }
        result = strip_wrong_language_patches(patches, "en")
        assert "project_header" in result
        assert "scope_video" in result
        assert "budget_timeline" not in result


class TestFilterScopePhoto:
    def test_removes_scope_photo_for_video_only(self):
        sections = {
            "project_header": "<p>Title</p>",
            "scope_video": "<p>Video</p>",
            "scope_photo": "<p>N/A</p>",
        }
        result = filter_scope_photo(sections, [1, 2])
        assert "scope_photo" not in result
        assert "scope_video" in result

    def test_keeps_scope_photo_for_photography_archetype(self):
        sections = {
            "scope_video": "<p>Video</p>",
            "scope_photo": "<p>Real photo content</p>",
        }
        result = filter_scope_photo(sections, [5])
        assert "scope_photo" in result

    def test_keeps_scope_photo_for_key_visual_archetype(self):
        sections = {
            "scope_photo": "<p>Key visual stills</p>",
        }
        result = filter_scope_photo(sections, [6])
        assert "scope_photo" in result

    def test_keeps_scope_photo_for_mixed_archetypes(self):
        sections = {
            "scope_photo": "<p>Stills + key visual</p>",
        }
        result = filter_scope_photo(sections, [2, 5])
        assert "scope_photo" in result

    def test_no_archetypes_drops_scope_photo(self):
        sections = {
            "scope_photo": "<p>N/A</p>",
        }
        result = filter_scope_photo(sections, [])
        assert "scope_photo" not in result

    def test_no_scope_photo_key_passthrough(self):
        sections = {"project_header": "<p>Title</p>"}
        result = filter_scope_photo(sections, [1])
        assert result == sections

    def test_handles_none_archetypes(self):
        sections = {"scope_photo": "<p>x</p>"}
        result = filter_scope_photo(sections, None)
        assert "scope_photo" not in result
