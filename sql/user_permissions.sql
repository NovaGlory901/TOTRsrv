-- Run this on the database server as a superuser / the database owner (e.g. in pgAdmin), in your TOTR database. The app never runs it.
-- Adds per-user permissions to a database made BEFORE they existed (auth_schema.sql already has them). Safe to run twice.
--
-- What it does
--  * users.permissions   : which extra things a member may do (for example "upload,collector"). Admins always
--                          have everything, so theirs is ignored. Existing users get '' (nothing extra), which is
--                          exactly what they can do today.
--  * invites.permissions : the permissions an invited person will start with, set when the link is made.
--
-- No new grants are needed: totr_auth already has SELECT/INSERT/UPDATE/DELETE on both tables, and that covers new
-- columns. The read-only "robots" role still has no access to them.
--
-- Order: run this first, then pull and restart the app. (If the app is restarted before this runs, it keeps working
-- with the old two roles and the permission screens say what is missing.)

ALTER TABLE totr.users   ADD COLUMN IF NOT EXISTS permissions text NOT NULL DEFAULT '' CHECK (permissions ~ '^[a-z_,]*$');
ALTER TABLE totr.invites ADD COLUMN IF NOT EXISTS permissions text NOT NULL DEFAULT '' CHECK (permissions ~ '^[a-z_,]*$');
