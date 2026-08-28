-- TIM RENO -- Item 2: Engineer login photo-proof task completion.
--
-- A completion photo is required going forward (enforced in
-- blueprints/tracker.py, not by a NOT NULL constraint here, since every
-- existing completed task has no photo and must remain valid). One photo
-- per task, never replaced once set -- see repositories/tracker.py:
-- save_task_photo / complete_task.

ALTER TABLE quote_tasks ADD COLUMN photo_filename TEXT;
