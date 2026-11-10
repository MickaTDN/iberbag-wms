CREATE FUNCTION nkt_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE owner integer;cap integer;used bigint;entryrow record;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT c.client_id INTO owner FROM storage_contexts c JOIN nkt_owners n ON n.client_id=c.client_id JOIN clients a ON a.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.active AND a.active AND w.active;
 IF owner IS NULL THEN RAISE EXCEPTION 'NKT context required'; END IF;
 IF TG_TABLE_NAME='nkt_entries' THEN
  IF TG_OP='UPDATE' AND OLD.status='confirmed' THEN RAISE EXCEPTION 'Confirmed NKT entry immutable'; END IF;
  IF COALESCE(jsonb_typeof(NEW.payload::jsonb->'rows'),'null')<>'array' OR jsonb_array_length(NEW.payload::jsonb->'rows') NOT BETWEEN 1 AND 60 THEN RAISE EXCEPTION 'Individual PEG rows required'; END IF;
  IF TG_OP='UPDATE' AND NEW.created_by<>OLD.created_by THEN RAISE EXCEPTION 'Entry author immutable'; END IF;
  IF TG_OP='INSERT' AND NEW.status<>'draft' THEN RAISE EXCEPTION 'Review before confirmation'; END IF;
  IF NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active AND role IN ('admin','operador')) THEN RAISE EXCEPTION 'Active user required'; END IF;
  IF NEW.status='confirmed' THEN
   IF NEW.confirmed_by IS DISTINCT FROM (SELECT username FROM users WHERE id=NEW.created_by) THEN RAISE EXCEPTION 'Confirmation actor mismatch'; END IF;
   IF NOT EXISTS(SELECT 1 FROM nkt_reviews WHERE context_id=NEW.context_id AND entry_id=NEW.id AND version=NEW.version AND payload=NEW.payload)
     OR (SELECT count(*) FROM nkt_pallets WHERE entry_id=NEW.id AND context_id=NEW.context_id)<>jsonb_array_length(NEW.payload::jsonb->'rows') THEN RAISE EXCEPTION 'Incomplete NKT entry'; END IF;
  END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND active AND role IN ('admin','operador')) THEN RAISE EXCEPTION 'Active user required'; END IF;
  SELECT * INTO entryrow FROM nkt_entries WHERE id=NEW.entry_id AND context_id=NEW.context_id AND status='draft';
  IF entryrow.id IS NULL OR entryrow.created_by<>(SELECT id FROM users WHERE username=NEW.actor) THEN RAISE EXCEPTION 'Draft entry required'; END IF;
  IF TG_TABLE_NAME='nkt_pallets' THEN
   IF NEW.client_id<>owner OR btrim(NEW.peg)='' OR NEW.peg<>btrim(NEW.peg) OR NEW.peg ~ '[[:cntrl:]]' OR NOT EXISTS(SELECT 1 FROM locations WHERE context_id=NEW.context_id AND codigo=NEW.local AND active) THEN RAISE EXCEPTION 'PEG/location invalid'; END IF;
   IF NOT EXISTS(SELECT 1 FROM nkt_reviews WHERE context_id=NEW.context_id AND entry_id=entryrow.id AND version=entryrow.version AND payload=entryrow.payload)
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(entryrow.payload::jsonb->'rows') x WHERE x->>'peg'=NEW.peg AND x->>'local'=NEW.local) THEN RAISE EXCEPTION 'PEG not reviewed'; END IF;
   SELECT capacidade INTO cap FROM locations WHERE context_id=NEW.context_id AND codigo=NEW.local;
   SELECT count(*) INTO used FROM nkt_pallets WHERE context_id=NEW.context_id AND local=NEW.local;
   used=used+(SELECT coalesce(sum(quantidade),0) FROM stock WHERE context_id=NEW.context_id AND local=NEW.local)+(SELECT coalesce(sum(physical_quantity),0) FROM valmet_positions WHERE context_id=NEW.context_id AND local=NEW.local);
   IF cap IS NOT NULL AND used+1>cap THEN RAISE EXCEPTION 'NKT capacity exceeded'; END IF;
  ELSIF NEW.version<>entryrow.version OR NEW.payload<>entryrow.payload OR NEW.digest<>encode(sha256(convert_to(NEW.payload,'UTF8')),'hex') THEN RAISE EXCEPTION 'Review mismatch'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION nkt_location_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE used bigint;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT count(*) INTO used FROM nkt_pallets WHERE context_id=OLD.context_id AND local=OLD.codigo;
 IF used>0 THEN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'NKT location occupied'; END IF;
  IF NEW.codigo<>OLD.codigo OR NEW.context_id<>OLD.context_id OR NOT NEW.active OR (NEW.capacidade IS NOT NULL AND NEW.capacidade<used+(SELECT coalesce(sum(quantidade),0) FROM stock WHERE context_id=OLD.context_id AND local=OLD.codigo)) THEN RAISE EXCEPTION 'NKT location occupied'; END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;RETURN NEW;
END $$;
CREATE FUNCTION nkt_final() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM nkt_entries WHERE id=NEW.entry_id AND context_id=NEW.context_id AND status='confirmed') THEN RAISE EXCEPTION 'NKT pallet must be confirmed atomically'; END IF;
 RETURN NULL;
END $$;
CREATE FUNCTION nkt_combined_stock() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE used bigint;cap integer;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT count(*) INTO used FROM nkt_pallets WHERE context_id=NEW.context_id AND local=NEW.local;
 IF used=0 THEN RETURN NEW; END IF;
 SELECT capacidade INTO cap FROM locations WHERE context_id=NEW.context_id AND codigo=NEW.local;
 IF cap IS NOT NULL AND used+NEW.quantidade+(SELECT coalesce(sum(quantidade),0) FROM stock WHERE context_id=NEW.context_id AND local=NEW.local AND id<>NEW.id)>cap THEN RAISE EXCEPTION 'Combined NKT capacity exceeded'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER nkt_entries_guard BEFORE INSERT OR UPDATE ON nkt_entries FOR EACH ROW EXECUTE FUNCTION nkt_guard();
CREATE TRIGGER nkt_confirmed_entry BEFORE UPDATE OR DELETE ON nkt_entries FOR EACH ROW EXECUTE FUNCTION wms_immutable_confirmation();
CREATE TRIGGER nkt_review_guard BEFORE INSERT ON nkt_reviews FOR EACH ROW EXECUTE FUNCTION nkt_guard();
CREATE TRIGGER nkt_pallet_guard BEFORE INSERT ON nkt_pallets FOR EACH ROW EXECUTE FUNCTION nkt_guard();
CREATE TRIGGER nkt_owner_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_owners FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER nkt_review_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_reviews FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER nkt_pallet_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_pallets FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER nkt_locations BEFORE UPDATE OR DELETE ON locations FOR EACH ROW EXECUTE FUNCTION nkt_location_guard();
CREATE TRIGGER nkt_stock BEFORE INSERT OR UPDATE ON stock FOR EACH ROW EXECUTE FUNCTION nkt_combined_stock();
CREATE CONSTRAINT TRIGGER nkt_confirmation AFTER INSERT ON nkt_pallets DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nkt_final();
