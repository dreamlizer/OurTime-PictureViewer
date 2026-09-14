# Add pinyin people search and state-aware label editing

## Goal
- Make every existing people-search field support Chinese, full pinyin, pinyin fragments, and initials.
- Let unnamed and passerby labels open naming from either a single click or a double click.
- Keep confirmed-name labels double-click only.

## Inputs Ready
- All people search boxes already use `/api/people?q=`.
- `pypinyin` and common polyphonic-surname overrides already exist.
- Viewer label states already distinguish confirmed names, unnamed people, and passersby.

## Scope
- Add one shared SQLite search function backed by cached derived pinyin forms.
- Search both formal name and alias.
- Normalize case, spaces, punctuation, and common `ü` input.
- Use a short single-click delay for unnamed/passerby labels so a double click opens only once.

## Acceptance Criteria
- [ ] Chinese substring search still works.
- [ ] Full pinyin and pinyin fragments match names and aliases.
- [ ] Initials such as `zsf` match a three-character name.
- [ ] Common polyphonic surnames use the existing corrected reading.
- [ ] New or renamed people need no manual index maintenance or schema migration.
- [ ] Single and double click both open unnamed/passerby editing exactly once.
- [ ] A confirmed-name label ignores ordinary single click and opens on double click.
- [ ] Dragging a label never opens editing.

## Out of Scope
- Fuzzy typo correction, numeric T9, or similarity search.
- Face recognition, thresholds, or formal photo data.
- A persistent FTS migration unless actual scale proves it necessary.

## Clarity Gate
- Score: 98/100
- Automation level: Level 3
- Remaining assumption: “three letters” means pinyin initials rather than literal vowel finals.

## Notes for Codex
- Use isolated data and random ports.
- Do not restart the formal workbench.
- Do not stage unrelated person-template files.
