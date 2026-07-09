"""Search form tests · Тесты шаблонов поиска"""

from __future__ import annotations

from pathlib import Path

from app.search_form import (
    NamedSearchTemplate,
    SearchFormSnapshot,
    delete_named_template,
    load_named_templates,
    rename_named_template,
    save_named_templates,
    template_exists,
    upsert_named_template,
)


def test_upsert_and_delete_named_template(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "search_templates.json"
    monkeypatch.setattr("app.search_form.TEMPLATES_PATH", path)

    form = SearchFormSnapshot(hashtag="orphie", max_posts=100)
    upsert_named_template("Orphie", form)
    assert template_exists("Orphie")
    items = load_named_templates()
    assert len(items) == 1
    assert items[0].form.hashtag == "orphie"

    delete_named_template("Orphie")
    assert not template_exists("Orphie")
    assert load_named_templates() == []


def test_rename_named_template(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "search_templates.json"
    monkeypatch.setattr("app.search_form.TEMPLATES_PATH", path)

    upsert_named_template("old", SearchFormSnapshot(hashtag="tag"))
    assert rename_named_template("old", "new")
    assert template_exists("new")
    assert not template_exists("old")
    assert load_named_templates()[0].name == "new"


def test_rename_named_template_rejects_duplicate(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "search_templates.json"
    monkeypatch.setattr("app.search_form.TEMPLATES_PATH", path)

    save_named_templates(
        [
            NamedSearchTemplate("one", SearchFormSnapshot(hashtag="a")),
            NamedSearchTemplate("two", SearchFormSnapshot(hashtag="b")),
        ],
    )
    assert not rename_named_template("one", "two")
    assert template_exists("one")
    assert template_exists("two")
