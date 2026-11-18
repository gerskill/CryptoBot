// Liaison de l'app à TON projet Supabase (voir docs/APP_IPHONE.md, étape 1).
// Supabase → Project Settings → API : « Project URL » et la clé publique « anon ».
// Cette clé est faite pour être publique : sans connexion à ton compte, elle
// ne donne accès à rien (RLS, aucun droit `anon` sur la table snapshots).
// Ne mets JAMAIS ici la clé « service_role » ni ton mot de passe.
window.CRYPTOBOT_CONFIG = {
  supabaseUrl: '',
  supabaseAnonKey: '',
};
