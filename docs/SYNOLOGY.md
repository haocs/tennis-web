# Synology Container Manager deployment

This deployment is local-network-only. The application has no additional login. All visitors who reach its local endpoint share the intentionally published library and preferences.

## Prepare folders and permissions

1. Keep originals in the private input folder. The processor runs on the Mac with permission to read that location and write the separate published library.
2. Keep published content in the dedicated shared library folder. Store only deployment settings (`compose.yaml` and private `.env`) in a Container Manager project directory of your choice. Application source comes from GitHub and the runnable image comes from the registry; neither needs a source checkout in the tennis media folder. The container receives only the library mount.
3. Use File Station folder properties to find the actual NAS filesystem path of the published library and set `TENNIS_LIBRARY_PATH` in `.env`. The Mac's SMB mount path is not valid on the NAS host. Media storage can be moved independently by changing this mount setting.
4. The application runs as UID/GID 10001 and can join an additional group using `TENNIS_MEDIA_GID`. Set that to a NAS group that already has read/traverse permission on the published library. Verify access rather than broadly changing private-folder ACLs. The separate Docker data volume is owned by the application UID.
5. Confirm both partners can browse the published library using their own NAS accounts. The website uses its own container identity; DSM user ACLs are not applied separately to HTTP visitors.

## When an existing NAS account owns the published folders

A container does not automatically inherit that account's NAS administrator-group membership. Matching the file-owner UID alone may still leave access denied when folder rules grant only groups. Inspect the actual ownership and folder rules before changing permissions.

For this deployment option, grant the existing reader account a direct **Read** rule on the published library, inherited by its descendants. Give the same account a direct **Read & Write** rule only on a separate website-state folder. Preserve the existing partner and administrator entries. Do not grant administrator-group membership to the container or mount private originals.

Set `TENNIS_APP_UID` to the verified numeric file-owner/reader UID, `TENNIS_APP_GID` to its numeric group, and `TENNIS_DATA_PATH` to the separate state directory's native host path. Merge the optional override on any host with Docker Compose installed:

```sh
docker compose -f compose.yaml -f compose.synology.yaml config > compose.nas.yaml
```

Use that rendered image-only file in Container Manager. Keep its resolved paths out of the public repository. The override replaces only the `/data` volume with a native-host bind; the library remains read-only. This is a filesystem path **on the NAS**, not SQLite running over a Mac SMB client mount. The default deployment continues to use a named state volume.

Do not remove an old state volume when selecting a different state location. Back up and migrate existing preferences while stopped if it contains real user data. Verify database creation, both session discovery and media access, new processor publications, and persistence after restart.

## Create the project

- Install/open Container Manager using the NAS administrator's normal DSM session.
- First publish and verify a public release image using [release operations](RELEASE.md).
- Copy only `compose.yaml` and `.env.example` to the project directory; rename the latter to `.env`. Fill in the actual release image/digest, library path, and media group ID.
- Create a Container Manager project using that image-only Compose file. Pull/download the image and deploy the project. No Dockerfile or application source build is needed. If DSM labels its deployment operation “Build”, the Compose file still has no `build:` section; it uses the specified registry image.
- Check project logs and container health. Do not reuse a stopped, failed source-build project without explicitly updating its saved configuration. Do not Clean it as part of routine deployment.
- No SSH service needs to be enabled. If using SSH was already configured, the same Compose commands in the README can be used there.
- Compose binds the published port to `127.0.0.1:8765` on the NAS. Test internally before exposing it through the reverse proxy.

## Reverse proxy and local access

In DSM, open Control Panel > Login Portal > Advanced > Reverse Proxy and create a rule:

- Source: the chosen local hostname and port. LAN-only HTTP is an available deployment option; for HTTPS, use a certificate trusted by client devices.
- Destination: HTTP, hostname 127.0.0.1, port 8765.
- Preserve the original Host header, including the public-facing port (for example, `$http_host` in an explicit proxy Host header). Metadata and like updates validate the browser Origin against that host. No WebSocket support is needed.
- Allow only your actual LAN subnet through a Synology access control profile; deny other sources. If the hostname resolves to IPv6, also allow the actual LAN IPv6 prefix or use a verified IPv4-only entry point. Choose the real network ranges rather than copying generic subnets.
- Do not configure router port forwarding, public tunnels, or public hosting.
- Configure local DNS for the chosen hostname. A hostname is not protected merely because its name looks local.

Verify byte-range requests and seeking through the reverse proxy, including large MP4 and HEVC files. Configure suitable request/read timeouts if the NAS storage is slow. The app returns conditional media responses and supports HTTP 206 Range requests.

A reverse proxy alone does not inherit DSM login sessions. This project intentionally adds no website login, matching the agreed local-access model. Optional DSM identity is deferred until a supported, low-friction integration is verified.

## Acceptance checklist

- Container is healthy after host/container restart.
- Published media and metadata mounts are read-only; original recordings are not mounted.
- Website state persists after pulling a replacement image and recreating the service without deleting volumes.
- Both partners can access the library, play and seek videos, and see shared likes.
- A clip like from one browser appears in another; stale metadata edits conflict safely.
- Processing heartbeat is fresh, and stale/disconnected status is obvious.
- Local hostname works on phones and desktops; external/public access is not configured.
- Database backup and restore have been exercised before treating deployment as complete.

References:
- [Synology Login Portal](https://kb.synology.com/en-af/DSM/help/DSM/AdminCenter/system_login_portal?version=7)
- [Synology reverse proxy and access control profiles](https://kb.synology.com/index.php/th-th/DSM/help/DSM/AdminCenter/system_login_portal_advanced?version=7)
- [Flask file serving API](https://flask.palletsprojects.com/en/stable/api/)

## Updating an image in the DSM interface

The supplied Compose file is image-only. Pull the selected release before deploying
updated project settings. Simply restarting an existing container does not select
a different image. The YAML Configurations screen supports deploying changed
project settings. Preserve the separate state volume and back it up before any
resource-removing operation. Synology documents Clean as potentially removing
containers, networks, volumes, and images; do not treat it as a harmless restart.

[Synology project operations](https://kb.synology.com/tr-tr/DSM/help/ContainerManager/docker_project?version=7)
