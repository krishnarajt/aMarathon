# Amarathon Backend

FastAPI backend for Amarathon. Local username/password authentication remains
supported. Authentik is an optional additional sign-in method that users link
to an existing player from the frontend profile.

## Authentik and API Get Away

Authentik proves the user's identity to API Get Away. The gateway then injects
`x-user-sub`, `x-user-email`, and `x-user-name` while proxying the request.
Because Amarathon also has a direct public URL, those headers alone are
spoofable. `GATEWAY_SHARED_SECRET` proves the request came through API Get Away:
the gateway strips any client-provided `x-api-gateway-secret`, injects the real
one, and Amarathon compares it before trusting the user headers.

The secret does not replace Authentik and does not identify users. It
authenticates only the API Gateway → Amarathon connection.

### 1. Generate and store the shared value

Generate the value once, then write that same value to all three Vault records:

```bash
export NEW_GATEWAY_SHARED_SECRET="$(openssl rand -hex 32)"

vault kv patch apps/api-get-away/api-get-away/env \
  GATEWAY_SHARED_SECRET="$NEW_GATEWAY_SHARED_SECRET"
vault kv patch apps/just-like-clockwork-backend/just-like-clockwork-backend/env \
  GATEWAY_SHARED_SECRET="$NEW_GATEWAY_SHARED_SECRET"
vault kv patch apps/amarathon/amarathon/env \
  GATEWAY_SHARED_SECRET="$NEW_GATEWAY_SHARED_SECRET"

unset NEW_GATEWAY_SHARED_SECRET
```

Do not generate three separate values and do not commit the value. Add your KV
mount prefix to the paths if required by your Vault installation.

### 2. Configure API Get Away

Add the mapping and deployed frontend origin using the authenticated gateway
admin UI/API:

```json
{
  "allowedOrigins": ["https://amarathon.vercel.app"],
  "mappings": [
    {"name": "amarathon", "backend": "http://100.69.148.22:32040"}
  ]
}
```

Merge these with existing entries instead of replacing the full configuration.
On an existing gateway deployment, active configuration is stored in Postgres;
editing `config.yml` changes only the empty-database bootstrap seed.

### 3. Migrate and restart

```bash
alembic upgrade head
kubectl -n api-gateway rollout restart deployment/api-gateway
kubectl -n amarathon rollout restart deployment/amarathon

kubectl -n api-gateway rollout status deployment/api-gateway
kubectl -n amarathon rollout status deployment/amarathon
```

External Secrets populates `amarathon-env` from
`apps/amarathon/amarathon/env`. Confirm that a 64-character value arrived
without printing it:

```bash
kubectl -n amarathon get secret amarathon-env \
  -o jsonpath='{.data.GATEWAY_SHARED_SECRET}' | base64 -d | wc -c
```

### Account-safe linking flow

1. Existing players continue signing in with their username and password.
2. They select **Profile → Sign-in methods → Link Authentik**.
3. Linking adds an external identity row to the existing user ID; it does not
   modify the password, username, challenges, friends, entries, or settings.
4. Authentik sign-in succeeds only for a previously linked identity.
5. An unlinked identity cannot automatically create, merge, replace, or delete
   a player account.

The required backend environment keys are documented in `.env.example`. The
new identity table is created by `alembic/versions/0002_external_identities.py`.
