# Publish and deploy a release

## First publication

1. Create a public GitHub repository for this website source only. Do not publish the parent workspace, NAS folders, deployment `.env`, personal screenshots, footage, databases, or live processing configuration.
2. Push this directory as the repository root on `main`. The included Actions workflow will run tests and a container smoke check. No external registry secret is required; publication uses the repository's built-in `GITHUB_TOKEN` with `packages: write` permission only in the release job.
3. Review the successful checks, then publish a GitHub release using a tag such as `v0.1.0`. Tags must use `vMAJOR.MINOR.PATCH`. Do not move/reuse published release tags.
4. Wait for both workflow jobs to succeed. Open the linked package and change its visibility to **Public**. GitHub initially creates packages privately unless configured otherwise. This is a one-time package setting, distinct from repository visibility.
5. Copy the immutable `TENNIS_IMAGE=ghcr.io/owner/repository@sha256:...` reference from the workflow summary.
6. Verify the image can be pulled anonymously before NAS deployment. On a Docker host, use an empty temporary Docker client configuration:

```sh
# Set IMAGE to the real digest from the successful workflow summary.
IMAGE=ghcr.io/owner/repository@sha256:REPLACE_WITH_REAL_DIGEST
ANONYMOUS_CONFIG="$(mktemp -d)"
docker --config "$ANONYMOUS_CONFIG" pull "$IMAGE"
rmdir "$ANONYMOUS_CONFIG"
```

The commands above are a template, not a claim that this package exists. Never deploy the example owner/digest. Successful anonymous download verifies public access; inspect the published manifest for the target architecture if deploying to another host type.

## NAS settings

Copy `compose.yaml` plus `.env.example` to a deployment directory, rename the latter to `.env`, and set the real image reference and host library path. There is no `build:` setting. Follow [Synology deployment](SYNOLOGY.md) for Container Manager and the LAN-only reverse proxy. Keep the deployment project name stable so its existing state volume is reused.

Do not use Synology Clean or volume deletion to update an image. If a failed experimental project already exists, leave it stopped until its removal is explicitly authorized. A new image-based project can be deployed independently; choose the existing intended state volume explicitly if it contains preferences that must be retained.

## Later releases

Publish a new version tag after review. Once checks and image publication succeed, back up website state, record the previous image digest, update only `TENNIS_IMAGE`, pull, and recreate the service. Verify health, library playback/seek, shared likes, and persistence. Roll back to the recorded digest if needed; database schema changes may also require restoring a compatible state backup.

## What has and has not been verified

Local application tests can run without Docker. `scripts/container-smoke.sh` needs a working Docker daemon and is run by GitHub CI against a disposable test image and synthetic empty library. It verifies UID 10001 startup under a read-only root filesystem, read-only library access, health, and data persistence across restart. The CI publication job builds both Intel/AMD and ARM variants; the smoke check currently runs on the CI runner's native Intel/AMD platform.

A workflow file alone does not establish a successful image build, package visibility, or a working NAS deployment. Verify those outcomes before advertising the site as live.

References: [GitHub container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry), [publishing images with GitHub Actions](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images), [Docker build action](https://github.com/docker/build-push-action).
