  ELSIF NEW.kind IN('cosmo_place','cosmo_vacate','cosmo_approve') THEN
    IF NOT EXISTS(SELECT 1 FROM cosmo_operations a JOIN cosmo_pallets b ON b.id=a.pallet_id AND b.context_id=a.context_id
       JOIN cosmo_states c ON c.pallet_id=b.id AND c.context_id=b.context_id
       WHERE a.id=NEW.ref_id AND a.context_id=NEW.context_id AND a.actor_id=u.id
       AND ((NEW.kind='cosmo_vacate' AND a.kind='place' AND a.vacated AND (a.before_state::jsonb->>'local')=p.label
             AND h.source_kind='cosmopak' AND h.source_id=b.id AND h.context_id=NEW.context_id AND h.state='pending_putaway')
         OR (NEW.kind='cosmo_place' AND a.kind='place' AND c.local=p.label AND c.status='placed'
             AND (h.state='free' OR (h.state='pending_putaway' AND h.source_kind='cosmopak' AND h.source_id=b.id AND h.context_id=NEW.context_id)))
         OR (NEW.kind='cosmo_approve' AND a.kind='approve' AND u.role='admin' AND c.stock_id=NEW.stock_id AND c.status='approved'
             AND h.state='pending_putaway' AND h.source_kind='cosmopak' AND h.source_id=b.id AND h.context_id=NEW.context_id))) THEN
      RAISE EXCEPTION 'Proven Cosmopak physical command required' USING ERRCODE='23514';END IF;
    IF NEW.kind='cosmo_vacate' THEN
      n.state:='free';n.context_id:=NULL;n.stock_id:=NULL;n.source_kind:=NULL;n.source_id:=NULL;
    ELSIF NEW.kind='cosmo_place' THEN
      n.state:='pending_putaway';n.context_id:=NEW.context_id;n.stock_id:=NULL;n.source_kind:='cosmopak';
      SELECT pallet_id INTO n.source_id FROM cosmo_operations WHERE id=NEW.ref_id;
    ELSE
      n.state:='occupied';n.stock_id:=NEW.stock_id;n.source_kind:='stock';n.source_id:=NEW.stock_id;
    END IF;
