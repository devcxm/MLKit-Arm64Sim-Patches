$use_patched_mlkit = true

def local_mlkit_pods
  if $use_patched_mlkit
    patched_mlkit_pods
  else
    official_mlkit_pods
  end
end

def patched_mlkit_pods
  mlkit_patch = {
    :git => "https://github.com/devcxm/MLKit-Arm64Sim-Patches.git",
    :commit => "dbcd2f765b068298e6322a53d3a494ba2e47a41d"
  }

  pod "GoogleMLKit/BarcodeScanning", **mlkit_patch
  pod "MLKitBarcodeScanning", **mlkit_patch
  pod "MLKitCommon", **mlkit_patch
  pod "MLKitVision", **mlkit_patch
  pod "MLImage", **mlkit_patch
end
