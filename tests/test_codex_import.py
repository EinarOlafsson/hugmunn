from pathlib import Path
from hugmunn.core import skills


def test_directory_skill_preserves_resources_and_loads_lazily(tmp_path):
    source = tmp_path / 'source' / 'demo'
    source.mkdir(parents=True)
    (source / 'SKILL.md').write_text('---\nname: "Demo"\ndescription: >\n  A multiline\n  description.\n---\nUse references/guide.md.\n' + 'Instructions. ' * 3000)
    (source / 'references').mkdir()
    (source / 'references' / 'guide.md').write_text('Support')
    target = tmp_path / 'target'
    assert skills.import_directory(source.parent, target) == 1
    assert skills.import_directory(source.parent, target) == 0
    loaded = skills.load_all(target)
    assert len(loaded) == 1 and not loaded[0].default_on
    assert 'A multiline description.' in loaded[0].description
    assert loaded[0].approx_tokens < 200
    path = Path(loaded[0].source_path)
    assert path.parent.joinpath('references/guide.md').read_text() == 'Support'
    assert len(loaded[0].body) > 30000


def test_import_skips_links_and_never_executes(tmp_path):
    source = tmp_path / 'skill'
    source.mkdir()
    (source / 'SKILL.md').write_text('---\nname: safe\ndescription: test\n---\nBody')
    secret = tmp_path / 'private'
    secret.write_text('secret')
    (source / 'linked').symlink_to(secret)
    target = tmp_path / 'target'
    skills.import_directory(source, target)
    assert not list(target.rglob('linked'))
