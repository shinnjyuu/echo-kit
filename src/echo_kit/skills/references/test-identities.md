# Preparing project test identities

This is project implementation guidance, not an executable Kit account API. Apply it only when the requested cases need authentication. Kit auth login/check authenticates existing identities; it does not provision users, roles or tenants.

## Onboarding decisions

1. Identify the requested identity, role, permission and tenant needs. Skip account preparation for anonymous cases.
2. Inspect seeds, fixtures, registration APIs, management commands and existing account scripts. Reuse the project's normal path.
3. If no repeatable path exists, write a project-owned script in its usual script directory, or echo/ if no convention exists. Use the project's language and business APIs. Do not directly tamper with password hashes, sessions or permissions to bypass checks.
4. Resolve and verify the exact target before mutation. Default to local development; shared testing requires explicit project configuration and existing task authorization. Unknown or mismatched targets stop execution. Do not infer authorization from an environment name or this guide, and do not request approval again when already authorized.
5. Explicitly prepare the account, authenticate with the existing auth adapter, and perform a minimal role/tenant-sensitive check. Successful login alone does not establish all required permissions. Record not-needed, reused, created-and-verified, or still-needs-preparation; a script existing is not evidence of success.

Keep preparation separate from auth login/check, service startup and implicit Verify preparation. Do not create accounts on every login.

## Project script pseudocode

The operations below are placeholders for existing project functions, not new echo-kit commands or a universal script:

```text
requirements = requirements_for_requested_cases()
if no_authentication_needed(requirements):
    return public_result("not-needed")
target = resolve_exact_project_target()
require_allowed_environment_and_existing_authorization(target)
identity = project_stable_test_identity(requirements)
account = find_identity_in_target(identity, target)
created_id = none
if account is absent:
    # Use a project transaction/unique constraint keyed by identity AND tenant.
    # On a duplicate conflict, re-read and validate; never overwrite the winner.
    account, created_here = project_create_if_absent(identity, target, secret_input)
    if created_here:
        created_id = account.id
        record_owned_creation_immediately(created_id, target, lifetime, run_id)
require_expected_test_ownership_role_and_tenant(account, requirements)
# A collision or mismatch is a reportable conflict, not permission to take over.
login_result = authenticate_through_existing_adapter(account, secret_input)
require_login_success(login_result)
require_minimal_role_and_tenant_check(login_result, requirements)
return public_result("created-and-verified" if created_id else "reused",
                     account.id, expected_role, expected_tenant, verification="passed")
```

If authentication or identity checks fail, report a sanitized reason and whether an account was already created. Never reset an existing password, increase permissions or silently adopt a same-name account. Preserve creation evidence before verification can fail. Handle partial failures without blindly repeating mutations.

## Credentials and output

Use existing project secret injection. For Kit authentication, configured credential environment variables feed private adapter stdin. Do not put secrets in command arguments, case inputs, shared/private notes, console output, exception dumps or ordinary reports. Kit's OS credential storage holds authentication state, not a new account-password store. If a provisioning API returns a generated secret, hand it to the project's existing approved secret channel, never the public result. Avoid logging raw API responses.

Illustrative sanitized result (not a mandated protocol):

```json
{
  "preparation": "reused",
  "account_id": "fixture-user-42",
  "role": "test-reader",
  "tenant": "fixture-tenant",
  "verification": "passed",
  "lifetime": "reusable"
}
```

Only publish identifiers appropriate for shared project evidence; use aliases where necessary. Errors contain a reason and pending steps, never passwords, tokens or session bodies.

## Lifetime and cleanup

Reusable test identities remain available, with their purpose documented. Temporary identities belong to the case that created them: record exact IDs, target and run ownership immediately using its active evidence, and integrate with the project's existing cleanup adapter. Delete only identities actually created by that run, never reused ones or users selected by name prefix. If creation succeeded but verification/cleanup fails, retain IDs and pending-cleanup evidence. Use existing creates_tasks/active/cleanup conventions for cases with asynchronous or persistent side effects; do not invent a new account lifecycle in Kit.

## Project handoff checklist

Shared project docs describe the preparation command, account purpose, allowed targets, roles/tenants, secret source names (not values), verification evidence and cleanup policy. Personal machine paths belong in ignored private notes; secrets belong in neither document type. Preserve existing team rules and scripts. List unresolved requirements instead of claiming onboarding complete.

| Scenario | Required outcome |
| --- | --- |
| Anonymous cases | Skip preparation; no new account script required. |
| Existing suitable identity | Reuse and verify; no mutation. |
| No repeatable preparation path | Add a project-owned script, execute only within authorization, verify or report pending. |
| Existing role/tenant/password mismatch | Report conflict without takeover, password reset or privilege changes. |
| Shared target without authorization | Prepare reviewable code/docs if useful; do not mutate the target. |
| Temporary account cleanup fails | Preserve exact owned IDs and unresolved evidence; never delete reused identities. |
