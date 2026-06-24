# MLKit Arm64 Simulator Patches

Generate and publish CocoaPods-compatible Google ML Kit binaries that can link on
Apple Silicon iOS simulators.

This repository exists for projects that still depend on older Google ML Kit
binary pods whose official frameworks do not contain a usable
`arm64-apple-ios-simulator` slice. The generator downloads the official
CocoaPods podspecs and source archives, keeps the official file layout, and
converts selected static frameworks into xcframeworks with an additional
arm64-simulator slice.

## What This Patches

The current configs patch these pods:

- `MLImage`
- `MLKitCommon`
- `MLKitVision`

The barcode scanner pod itself is kept as the official framework:

- `MLKitBarcodeScanning`

`GoogleMLKit` is included because its subspecs and headers are still required by
consumers such as `GoogleMLKit/BarcodeScanning`.

## Repository Layout

- `main` contains the generator scripts and version configs.
- Version commits contain generated CocoaPods payloads at the repository root.
- Consumers should pin a generated commit, not a branch name.

The generated output directories on `main` are ignored:

- `v8/`
- `v9/`

These directories are local build output only. They are used to create version
commits.

## Use With CocoaPods

Pin the generated commit in your `Podfile`.

Do not use `:branch => 'v9'` for application dependencies. Version branches may
be regenerated; a commit is reproducible.

Current v9 generated commit:

```ruby
mlkit_patch = {
  :git => 'https://github.com/devcxm/MLKit-Arm64Sim-Patches.git',
  :commit => '37d3be832b8651c0862ff75098b5eddd6b0e46f8'
}

pod 'GoogleMLKit/BarcodeScanning', **mlkit_patch
pod 'MLKitBarcodeScanning', **mlkit_patch
pod 'MLKitCommon', **mlkit_patch
pod 'MLKitVision', **mlkit_patch
pod 'MLImage', **mlkit_patch
```

Then run:

```sh
pod install
```

## Local Pod Testing

For local testing before publishing a generated commit, point CocoaPods at the
generated output directory:

```ruby
local_mlkit_path = '/path/to/MLKit-Arm64Sim-Patches/v9'

pod 'GoogleMLKit/BarcodeScanning', :path => local_mlkit_path
pod 'MLKitBarcodeScanning', :path => local_mlkit_path
pod 'MLKitCommon', :path => local_mlkit_path
pod 'MLKitVision', :path => local_mlkit_path
pod 'MLImage', :path => local_mlkit_path
```

This is useful when changing the patch scripts or testing a new ML Kit version.
Do not commit local absolute paths to application projects.

## Non-Debug Build Guard

Generated pods can inject a compile-time guard:

```sh
python3 generate_official_patched_pods.py mlkit_v9_config.json --release-guard yes
```

When enabled, the generated podspecs add:

```text
-include "${PODS_ROOT}/Headers/Public/GoogleMLKit/MLKitReleaseBuildGuard.h"
```

The guard header fails compilation when `DEBUG` is not defined. This makes the
patched binaries difficult to ship accidentally in non-Debug builds.

If you intentionally want generated pods without this guard:

```sh
python3 generate_official_patched_pods.py mlkit_v9_config.json --release-guard no
```

## Generate A Version

From the repository root:

```sh
python3 generate_official_patched_pods.py mlkit_v9_config.json --release-guard yes
```

The script will:

1. Download official podspec JSON files from the CocoaPods Specs repository.
2. Read each podspec's `source.http`.
3. Download the official Google binary archives.
4. Remove simulator `EXCLUDED_ARCHS` settings from generated podspecs.
5. Convert selected frameworks into xcframeworks.
6. Write generated pods into the configured output directory.

The v9 config currently contains:

```text
GoogleMLKit 9.0.0
MLImage 1.0.0-beta8
MLKitCommon 14.0.0
MLKitVision 10.0.0
MLKitBarcodeScanning 8.0.0
```

The v8 config currently contains:

```text
GoogleMLKit 8.0.0
MLImage 1.0.0-beta7
MLKitCommon 13.0.0
MLKitVision 9.0.0
MLKitBarcodeScanning 7.0.0
```

## Version Commits

The old version-branch workflow is still useful as a publishing mechanism:

- `main` stores scripts and configs.
- `v8` or `v9` stores generated pod payloads at the repository root.
- Application projects should reference the generated commit hash.

In other words, branches are for maintainers; commits are for consumers.

## Notes

This project does not modify ML Kit source code. It repackages official binary
pods into a simulator-linkable layout for local development on Apple Silicon.

Only use this if you understand the risk of repackaging third-party binaries.
Prefer official Google releases whenever they support your target simulator
architecture.
