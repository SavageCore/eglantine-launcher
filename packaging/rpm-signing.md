# RPM signing for the dnf repo

`nobara-sync` / `nobara-updater` rejects unsigned RPMs even when the
`.repo` file says `gpgcheck=0`. It replays transactions with
`dnf5 ... --setopt=localpkg_gpgcheck=1`, so one unsigned package fails
the whole system update.

All RPMs and the dnf repodata must be signed. This uses a dedicated
repo-only key (not a personal key).

## Key

* Identity: `Eglantine Launcher <eglantine-launcher@savagecore.github.io>`
* Primary fingerprint: `DBF1B4CB826B8C196D097BE1FBCC400A05D93DF9`
* Public key: `packaging/RPM-GPG-KEY-eglantine-launcher`
* Published at: `https://savagecore.github.io/eglantine-launcher/dnf/RPM-GPG-KEY-eglantine-launcher`
* Repo config: `packaging/eglantine-launcher.repo` (`gpgcheck=1`, `repo_gpgcheck=1`)

## GitHub Secrets setup (one time)

The private key is never committed. Add it as an Actions secret:

```sh
# from a machine holding the private key
gh secret set RPM_SIGNING_KEY < /path/to/RPM-SIGNING-KEY-private.asc --repo SavageCore/eglantine-launcher
```

Verify:

```sh
gh secret list --repo SavageCore/eglantine-launcher
```

CI imports it in `pkgs` (for `rpm --addsign`) and in `repos` (for
signing `repodata/repomd.xml`). If the secret is missing (forks),
the build continues unsigned and prints a warning.

## What CI does

1. `pkgs`: builds `.rpm` with nfpm, then `rpm --addsign`, then `rpm -K`.
2. `repos`:
   * `createrepo_c _site/dnf`
   * `gpg --detach-sign --armor _site/dnf/repodata/repomd.xml`
   * copies `RPM-GPG-KEY-eglantine-launcher` and `eglantine-launcher.repo`
     into `_site/dnf/`
   * deploys with `keep_files: false`, so the next release wipes the old
     unsigned `*.rpm` files from `gh-pages` (purge).

## Migrating existing installs

Old installs use `gpgcheck=0` and no key. After the first signed release:

```sh
sudo rpm --import https://savagecore.github.io/eglantine-launcher/dnf/RPM-GPG-KEY-eglantine-launcher
sudo curl -o /etc/yum.repos.d/eglantine-launcher.repo https://savagecore.github.io/eglantine-launcher/dnf/eglantine-launcher.repo
sudo dnf clean expire-cache
sudo dnf install eglantine-launcher
```

## Rotating the key

1. Generate a new key with the same identity.
2. Replace `packaging/RPM-GPG-KEY-eglantine-launcher`.
3. Update `RPM_SIGNING_KEY` secret.
4. Tag a new release so repodata and RPMs are signed with the new key.
5. Tell users to re-run `rpm --import` for the new key.
