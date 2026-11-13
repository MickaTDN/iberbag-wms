  ELSIF NEW.kind IN('cosmo_stage_out','cosmo_return_hold','cosmo_return_commit') THEN
    IF NOT EXISTS(SELECT 1 FROM cosmo_packing_actions a WHERE a.id=NEW.ref_id AND a.context_id=NEW.context_id AND a.actor_id=u.id AND (a.payload::jsonb->>'physical')::boolean) THEN
      RAISE EXCEPTION 'Proven packing physical action required' USING ERRCODE='23514';END IF;
    IF NEW.kind='cosmo_stage_out' THEN
      IF h.state<>'occupied' OR h.context_id<>NEW.context_id OR h.stock_id IS DISTINCT FROM NEW.stock_id
        OR NOT EXISTS(SELECT 1 FROM cosmo_packing_moves m WHERE m.action_id=NEW.ref_id AND m.context_id=NEW.context_id AND (m.before_state::jsonb->>'stock_id')::integer=NEW.stock_id AND m.before_state::jsonb->>'local'=p.label AND m.after_state::jsonb->>'local'='Zona de preparação')
        OR EXISTS(SELECT 1 FROM stock WHERE id=NEW.stock_id AND quantidade<>0) THEN RAISE EXCEPTION 'Physical rack movement not confirmed' USING ERRCODE='23514';END IF;
      n.state:='free';n.context_id:=NULL;n.stock_id:=NULL;n.source_kind:=NULL;n.source_id:=NULL;
    ELSIF NEW.kind='cosmo_return_hold' THEN
      IF h.state<>'free' OR NOT EXISTS(SELECT 1 FROM cosmo_packing_actions WHERE id=NEW.ref_id AND kind='return_rack' AND payload::jsonb->>'local'=p.label) THEN RAISE EXCEPTION 'Return position occupied or unproven' USING ERRCODE='23514';END IF;
      n.state:='pending_transfer';n.context_id:=NEW.context_id;n.stock_id:=NULL;n.source_kind:='cosmo_packing';n.source_id:=NEW.ref_id;
    ELSE
      IF h.state<>'pending_transfer' OR h.source_kind IS DISTINCT FROM 'cosmo_packing' OR h.source_id<>NEW.ref_id OR h.context_id<>NEW.context_id
       OR NOT EXISTS(SELECT 1 FROM cosmo_packing_moves m WHERE m.action_id=NEW.ref_id AND m.context_id=NEW.context_id AND (m.after_state::jsonb->>'stock_id')::integer=NEW.stock_id AND m.after_state::jsonb->>'local'=p.label) THEN RAISE EXCEPTION 'Packing return identity changed' USING ERRCODE='23514';END IF;
      n.state:='occupied';n.stock_id:=NEW.stock_id;n.source_kind:='stock';n.source_id:=NEW.stock_id;
    END IF;
