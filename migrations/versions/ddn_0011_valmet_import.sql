CREATE FUNCTION valmet_import_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE d record;r record;e record;data jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND active AND role='admin') OR NOT EXISTS(
 SELECT 1 FROM storage_contexts c JOIN valmet_owners v ON v.client_id=c.client_id JOIN clients a ON a.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.active AND a.active AND w.active)
 OR NEW.digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'Historical import requires administrator/context/digest'; END IF;
 IF TG_TABLE_NAME='valmet_import_revisions' THEN
   SELECT * INTO d FROM valmet_import_drafts WHERE id=NEW.draft_id AND context_id=NEW.context_id;
   IF d.id IS NULL THEN RAISE EXCEPTION 'Invalid source preview'; END IF;
 ELSIF TG_TABLE_NAME='valmet_import_confirmations' THEN
   SELECT * INTO r FROM valmet_import_revisions WHERE id=NEW.revision_id AND context_id=NEW.context_id;
   SELECT * INTO d FROM valmet_import_drafts WHERE id=r.draft_id AND context_id=NEW.context_id;
   SELECT * INTO e FROM valmet_entries WHERE id=NEW.entry_id AND context_id=NEW.context_id AND status='draft' AND xmin=pg_current_xact_id()::xid;
   IF r.id IS NULL OR e.id IS NULL OR d.digest<>NEW.digest OR NEW.client_id<>(SELECT client_id FROM storage_contexts WHERE id=NEW.context_id)
      OR (e.payload::jsonb->'rows') IS DISTINCT FROM (r.plan::jsonb->'rows') OR COALESCE((r.plan::jsonb->>'blocking')::integer,-1)<>0 OR COALESCE(jsonb_typeof(r.plan::jsonb->'rows'),'null')<>'array' OR jsonb_array_length(r.plan::jsonb->'rows')=0
      OR e.created_by<>(SELECT id FROM users WHERE username=NEW.actor) OR NOT COALESCE((r.plan::jsonb->>'acknowledged')::boolean,false)
      OR (SELECT count(*) FROM valmet_units WHERE entry_id=e.id AND context_id=NEW.context_id)<>jsonb_array_length(r.plan::jsonb->'rows') THEN RAISE EXCEPTION 'Invalid historical import confirmation'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION valmet_imported_entry_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 IF TG_OP='INSERT' THEN
   IF NEW.status='imported' THEN RAISE EXCEPTION 'Historical entry must be staged first'; END IF;
   RETURN NEW;
 END IF;
 IF OLD.status='imported' THEN RAISE EXCEPTION 'Imported history is immutable'; END IF;
 IF TG_OP='UPDATE' AND NEW.status='imported' AND NOT EXISTS(SELECT 1 FROM valmet_import_confirmations WHERE entry_id=NEW.id AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Historical entry requires confirmed provenance'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;RETURN NEW;
END $$;
CREATE TRIGGER valmet_imported_entry BEFORE INSERT OR UPDATE OR DELETE ON valmet_entries FOR EACH ROW EXECUTE FUNCTION valmet_imported_entry_guard();
CREATE TRIGGER valmet_import_draft_guard BEFORE INSERT ON valmet_import_drafts FOR EACH ROW EXECUTE FUNCTION valmet_import_guard();
CREATE TRIGGER valmet_import_revision_guard BEFORE INSERT ON valmet_import_revisions FOR EACH ROW EXECUTE FUNCTION valmet_import_guard();
CREATE TRIGGER valmet_import_confirmation_guard BEFORE INSERT ON valmet_import_confirmations FOR EACH ROW EXECUTE FUNCTION valmet_import_guard();
CREATE TRIGGER valmet_import_draft_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_import_drafts FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_import_revision_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_import_revisions FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_import_confirmation_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_import_confirmations FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE FUNCTION valmet_import_final() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM valmet_entries WHERE id=NEW.entry_id AND context_id=NEW.context_id AND status='imported') THEN RAISE EXCEPTION 'Historical import must finalize atomically'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER valmet_import_final AFTER INSERT ON valmet_import_confirmations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_import_final();
