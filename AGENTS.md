<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Forge (Python agent) rules

- All model calls go through `FleetClient` (`forge/models/client.py`), never a
  single-provider client — it owns one `GroqClient`-style transport per provider
  and routes by model name, so adding a provider needs no caller changes.
- Model choice per phase goes through `ModelRouter.choose(phase, need, client)`
  (`forge/models/router.py`), which walks `Config.candidates()` and picks the
  first model with live per-key TPM/RPM headroom — keeps free tiers from 429ing.
- Rate limits and key rotation live in `KeyManager` (per provider, per key, per
  model, 60s sliding window); nothing else may track usage, or the budget splits.
- Per-key model support lives on `KeyState` (declared via `<KEY_NAME>_MODELS` in .env, learned live from 404s); KeyManager/router skip keys and models that can't serve — so retired models only hit keys that still have them.
