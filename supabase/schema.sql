-- CryptoBot : résumés publiés par le Mac pour l'app iPhone (lecture seule)
-- À coller dans Supabase → SQL Editor → New query, puis « Run ». Peut être relancé sans risque.
--
-- Une ligne par « clé » et par utilisateur, réécrite toutes les 2 min par
-- scripts/publie_supabase.py avec le compte de Killian (jamais la clé secrète du projet) :
--   etat, positions, perf_7d, perf_30d, perf_all, liquid, alertes
-- L'app (mobile/) ne fait que lire. Aucune suppression n'est autorisée.
-- Contrat des champs : docs/superpowers/plans/2026-11-18-app-iphone.md.

create table if not exists public.snapshots (
  user_id      uuid        not null default auth.uid() references auth.users (id) on delete cascade,
  key          text        not null check (char_length(key) between 1 and 64),
  value        jsonb       not null,
  published_at timestamptz not null default now(),
  primary key (user_id, key)
);

-- Chacun ne voit et n'écrit que ses propres lignes
alter table public.snapshots enable row level security;

drop policy if exists "snapshots: lire ses lignes"     on public.snapshots;
drop policy if exists "snapshots: créer ses lignes"    on public.snapshots;
drop policy if exists "snapshots: modifier ses lignes" on public.snapshots;

create policy "snapshots: lire ses lignes" on public.snapshots
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "snapshots: créer ses lignes" on public.snapshots
  for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "snapshots: modifier ses lignes" on public.snapshots
  for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

revoke all on public.snapshots from anon;
grant select, insert, update on public.snapshots to authenticated;
