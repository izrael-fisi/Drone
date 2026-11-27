# Global View enterprise operations

The enterprise portal reads each site's gateway through the existing organization-scoped routes. Search indexing, case history, procedure state, and held clips live at the site. The recorder remains the source for continuous video.

## Run a guided incident

1. In **Posture → Site configuration and versions**, open a site draft and expand **Response procedures**. Choose Forced door, Held-open door, or Loss of critical camera coverage. A procedure can apply to a specific boundary. For door procedures, an unscoped definition applies where a boundary-specific definition is absent.
2. Add a template, edit its instructions, connect the next steps, and set the escalation interval. Save the draft and publish it. The gateway validates the procedure with the rest of the configuration.
3. In **Alerts**, open the incident, take ownership, and work through **Guided response**. Use the evidence link to open replay. The incident records who answered each step and retains the configuration version used to create the procedure.
4. Use the existing response handoff to transfer responsibility. A supervisor can record an override reason. A procedure must finish or be overridden before resolution.
5. Configure the gateway's existing `--alert-webhook` to send security and overdue notifications. The receiver should deduplicate by `deliveryId`.

For critical coverage, assign a coverage procedure to an enabled boundary and field-check its cameras. The gateway requires a previously usable view and 60 seconds with all qualifying views known to be unavailable. Missing or stale checks show as unverified. Coverage recovery adds an event to the existing incident and leaves the operator in charge of its disposition. [Genetec dynamic procedures](https://techdocs.genetec.com/r/en-US/Genetec-Mission-ControlTM-Administrator-Guide-3.4.0.0/Configuring-dynamic-SOPs-for-Mission-Control)

## Search evidence and export a case

Open **Search → Evidence search and cases** or the same workspace from **Alerts**. Filter by site, UTC dates, device and boundary IDs, event, object class, or text. Results list the indexed history available under the active account. If a site is unreachable, search again once it reconnects. The gateway uses SQLite FTS5 where available and basic text matching on SQLite builds without it.

Start a case from an incident or matching result. Collect camera observations or bookmarks from the case's site. Playback uses the available clip around the indexed event time. The case records its evidence references even if the recorder can no longer supply footage. Case recording retrieval can require a wait while the recorder makes the event interval available.

A supervisor can hold up to four clips per signed case export. Select the held items and use **Export signed case**. An operator can request supervisor export from the same case. The ZIP contains the report, a manifest with file checksums, held clips, and a detached signature. Verify it with the site's separately trusted public key.

```bash
python services/camera-gateway/verify_evidence.py proxigo-case-evidence.zip trusted-site-key.pem
```

The verifier also accepts the previous incident export format. [Milestone incident projects](https://doc.milestonesys.com/xprotect/xprotect-incident-manager-for-users/2026r2/en/incident-projects-and-properties)

## Import a site inventory

From **Posture → Site configuration and versions → Import site inventory**, select device CSV, boundary GeoJSON, or both.

```csv
id,name,type,longitude,latitude,floorId,boundaryIds,linkedCameraId,labels
dock-cam,Dock camera,camera,-122.30,37.78,,dock-yard,,outside|priority
dock-door,Dock door,door,-122.30,37.78,,dock-yard,dock-cam,access
```

The five required device columns are `id,name,type,longitude,latitude`. Supported optional columns are `floorId,groupId,boundaryIds,linkedCameraId,labels`. Use `|` within list cells. A present blank cell clears an optional value. An omitted column preserves the existing value.

Use a WGS 84 GeoJSON FeatureCollection with a stable string `id`, a single closed exterior Polygon ring, and `name`, `kind`, and optional `floorId` properties. Allowed boundary kinds are Perimeter, Restricted, and Access. New boundaries are paused. Fix any preview errors, inspect the draft on the map, apply the import, and publish the saved draft separately. [GeoJSON format](https://www.rfc-editor.org/info/rfc7946/)

In **Posture**, use **Site insights** for modeled coverage, site field checks, live video, recorder drill results, and rule validation. Use **Site operations and connectors → Record rule validation** to document the evidence reviewed for a published rule, camera and model. Record site acceptance using independently labeled footage and the customer's threshold. A rule validation entry is a supervisor's sign-off.

## Connect a VMS

Qualify the exact licensed installation before using these read-only features in customer operations. Keep credentials and TLS trust configuration in private files. Use the site's existing XProtect catalog, event, and WebRTC options. Add `--vms-ca /private/vms-ca.pem` when the approved VMS TLS endpoint uses a private certificate authority.

For Security Center, enable the configured Web SDK and Access Manager event capture through the system administrator. Use:

- `--genetec-origin https://security-center.example:4590`
- `--genetec-credentials /private/genetec-credentials.json` with `username`, `applicationId`, and `password`
- `--genetec-device-map /private/genetec-devices.json` with vendor entity GUID keys and published Proxigo device ID values
- `--genetec-stream-port 4591` for the approved TLS event streaming port

Open **Genetec device catalog** in a site draft to add cameras and doors, map them to their locations, and link cameras and boundaries before publishing. The live subscription wakes the durable Access Manager report reader. The report positions control acceptance and deduplication. The gateway uses a short initial catch-up window and tracks cursors across restarts. For Genetec event evidence, use a separately registered site recorder until a Security Center media path is qualified. See connector status in **Posture → Site operations and connectors**. [Genetec Web SDK authentication](https://github.com/Genetec/DAP/wiki/web-sdk-getting-started) · [Genetec access-event reports](https://github.com/Genetec/DAP/wiki/web-sdk-reports) · [XProtect APIs](https://doc.developer.milestonesys.com/mipvmsapi/api-overview/)

## Back up and restore a site

Schedule a maintenance window and stop the gateway for the coordinated snapshot. Back up into a new file using an administrator-managed X.509 encryption certificate. Keep the private key separate from the backup. Run the commands on protected local storage because the utility stages temporary plaintext files while creating or restoring the snapshot.

```bash
python services/camera-gateway/gateway_backup.py backup \
  --state-dir /private/gateway-state --archive /private/backups/site.p7m \
  --recipient-cert /private/backup-recipient.pem

python services/camera-gateway/gateway_backup.py restore \
  --state-dir /private/gateway-restored --archive /private/backups/site.p7m \
  --recipient-cert /private/backup-recipient.pem --recipient-key /private/backup-recipient.key \
  --approved-access /private/current-site-access.json
```

Use the current approved account roster in the same site access format as the gateway. The restore checks the organization, site and directory tenant, replaces restored membership with that roster, rejects reused site token hashes, and invalidates old viewing links. Supply newly issued token hashes for local-token access or leave them out for OIDC-only access. Keep private deployment files for adapter credentials, TLS, and the enterprise portal with the deployment configuration. Restart the gateway pointed at the new state directory and verify evidence, pending notifications, a valid operator, and a revoked operator before opening the site to users.

For optional AI drafting, device degradation, gate corroboration, and clip review, see the [site intelligence pilot](global-view-ai-pilot.md).

## Verification

Use the pinned model and local recorder, ONVIF, OPC UA, Chrome, and MediaMTX prerequisites from the [industry gate guide](global-view-vnext.md#run-the-verification-gates). Run the `enterprise-operations` gate for the focused contracts and workflows or `enterprise-integrated` for the prior workflows, enterprise isolation, 12-camera tests, and 570-case baseline comparison.

```bash
python scripts/dev/run_industry_vision_gate.py enterprise-operations --output-dir /private/verification
python scripts/dev/run_industry_vision_gate.py enterprise-integrated \
  --provider CoreMLExecutionProvider --reference-report /private/baseline/meva-corpus.json \
  --output-dir /private/verification
```

Physical recorder firmware, licensed VMS versions, customer identity, site recovery, and detector quality on installed cameras require their own acceptance.
