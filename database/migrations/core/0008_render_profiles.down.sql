DROP FUNCTION IF EXISTS catalog.render_profile_for(text, integer);
DROP TRIGGER IF EXISTS render_profiles_guard ON catalog.render_profiles;
DROP TABLE IF EXISTS catalog.render_profiles;
DROP FUNCTION IF EXISTS catalog.render_profiles_guard();
