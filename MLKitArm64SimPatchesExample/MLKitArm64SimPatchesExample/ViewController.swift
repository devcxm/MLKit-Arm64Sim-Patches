//
//  ViewController.swift
//  MLKitArm64SimPatchesExample
//
//  Created by chi on 2026/6/24.
//

import AVFoundation
import AudioToolbox
import UIKit
import MLKitBarcodeScanning
import MLKitVision

final class ViewController: UIViewController {
    private let captureSession = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let captureQueue = DispatchQueue(label: "com.mlkit-arm64sim-patches.camera")
    private let qrScanner = BarcodeScanner.barcodeScanner(options: BarcodeScannerOptions(formats: .qrCode))
    private let allScanner = BarcodeScanner.barcodeScanner(options: BarcodeScannerOptions(formats: .all))

    private var previewLayer: AVCaptureVideoPreviewLayer?
    private var isProcessingFrame = false
    private var frameCount = 0
    private var scanCount = 0

    private let statusLabel: UILabel = {
        let label = UILabel()
        label.translatesAutoresizingMaskIntoConstraints = false
        label.numberOfLines = 0
        label.textAlignment = .center
        label.textColor = .white
        label.font = .systemFont(ofSize: 16, weight: .semibold)
        label.backgroundColor = UIColor.black.withAlphaComponent(0.65)
        label.layer.cornerRadius = 8
        label.layer.masksToBounds = true
        label.text = "Requesting camera access..."
        return label
    }()

    override func viewDidLoad() {
        super.viewDidLoad()

        view.backgroundColor = .black
        view.addSubview(statusLabel)
        NSLayoutConstraint.activate([
            statusLabel.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 16),
            statusLabel.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -16),
            statusLabel.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -24)
        ])

        requestCameraAccess()
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer?.frame = view.bounds
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        stopCaptureSession()
    }

    private func requestCameraAccess() {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            configureCaptureSession()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                DispatchQueue.main.async {
                    if granted {
                        self?.configureCaptureSession()
                    } else {
                        self?.showStatus("Camera access denied")
                    }
                }
            }
        case .denied, .restricted:
            showStatus("Camera access denied")
        @unknown default:
            showStatus("Camera access unavailable")
        }
    }

    private func configureCaptureSession() {
        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            showStatus("Camera unavailable")
            return
        }

        do {
            try configureCamera(camera)
            let input = try AVCaptureDeviceInput(device: camera)

            captureSession.beginConfiguration()
            captureSession.sessionPreset = .hd1280x720

            if captureSession.canAddInput(input) {
                captureSession.addInput(input)
            }

            videoOutput.alwaysDiscardsLateVideoFrames = true
            videoOutput.setSampleBufferDelegate(self, queue: captureQueue)
            videoOutput.videoSettings = [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
            ]

            if captureSession.canAddOutput(videoOutput) {
                captureSession.addOutput(videoOutput)
            }

            if let connection = videoOutput.connection(with: .video), connection.isVideoOrientationSupported {
                connection.videoOrientation = .portrait
            }

            captureSession.commitConfiguration()
            installPreviewLayer()
            startCaptureSession()
            print("[QRScanner] Capture session configured")
            showStatus("Point the camera at a QR code")
        } catch {
            print("[QRScanner] Camera setup failed: \(error.localizedDescription)")
            showStatus("Camera setup failed: \(error.localizedDescription)")
        }
    }

    private func configureCamera(_ camera: AVCaptureDevice) throws {
        try camera.lockForConfiguration()
        defer { camera.unlockForConfiguration() }

        if camera.isFocusModeSupported(.continuousAutoFocus) {
            camera.focusMode = .continuousAutoFocus
        }

        if camera.isExposureModeSupported(.continuousAutoExposure) {
            camera.exposureMode = .continuousAutoExposure
        }

        if camera.isSmoothAutoFocusSupported {
            camera.isSmoothAutoFocusEnabled = true
        }
    }

    private func installPreviewLayer() {
        let layer = AVCaptureVideoPreviewLayer(session: captureSession)
        layer.videoGravity = .resizeAspectFill
        layer.frame = view.bounds
        view.layer.insertSublayer(layer, at: 0)
        previewLayer = layer
    }

    private func startCaptureSession() {
        captureQueue.async { [captureSession] in
            if !captureSession.isRunning {
                captureSession.startRunning()
            }
        }
    }

    private func stopCaptureSession() {
        captureQueue.async { [captureSession] in
            if captureSession.isRunning {
                captureSession.stopRunning()
            }
        }
    }

    private func showStatus(_ text: String) {
        DispatchQueue.main.async { [weak self] in
            self?.statusLabel.text = text
        }
    }

    private func statusText(for barcodes: [Barcode]) -> String {
        scanCount += 1

        let barcodeLines = barcodes.prefix(4).map { barcode in
            let value = barcode.rawValue ?? barcode.displayValue ?? "Unsupported barcode"
            return "\(formatName(for: barcode.format)): \(value)"
        }

        return (["Scan #\(scanCount)"] + barcodeLines).joined(separator: "\n")
    }

    private func imageOrientation() -> UIImage.Orientation {
        switch UIDevice.current.orientation {
        case .portrait:
            return .right
        case .portraitUpsideDown:
            return .left
        case .landscapeLeft:
            return .up
        case .landscapeRight:
            return .down
        default:
            return .right
        }
    }

    private func formatName(for format: BarcodeFormat) -> String {
        switch format {
        case .code128:
            return "Code 128"
        case .code39:
            return "Code 39"
        case .code93:
            return "Code 93"
        case .codaBar:
            return "Codabar"
        case .dataMatrix:
            return "Data Matrix"
        case .EAN13:
            return "EAN-13"
        case .EAN8:
            return "EAN-8"
        case .ITF:
            return "ITF"
        case .qrCode:
            return "QR Code"
        case .UPCA:
            return "UPC-A"
        case .UPCE:
            return "UPC-E"
        case .PDF417:
            return "PDF417"
        case .aztec:
            return "Aztec"
        default:
            return String(describing: format)
        }
    }
}

extension ViewController: AVCaptureVideoDataOutputSampleBufferDelegate {
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard !isProcessingFrame else { return }
        isProcessingFrame = true

        frameCount += 1
        let currentFrame = frameCount
        let orientation = imageOrientation()
        let image = VisionImage(buffer: sampleBuffer)
        image.orientation = orientation

        if currentFrame == 1 || currentFrame % 30 == 0 {
            print("[QRScanner] Processing frame #\(currentFrame), orientation=\(orientation)")
        }

        do {
            defer { isProcessingFrame = false }

            let qrBarcodes = try qrScanner.results(in: image)
            if !qrBarcodes.isEmpty {
                let status = statusText(for: qrBarcodes)
                print("[QRScanner] QR-only detected \(qrBarcodes.count) barcode(s) at frame #\(currentFrame): \(status.replacingOccurrences(of: "\n", with: " | "))")
                AudioServicesPlaySystemSound(kSystemSoundID_Vibrate)
                showStatus(status)
                return
            }

            let allBarcodes = try allScanner.results(in: image)
            guard !allBarcodes.isEmpty else {
                if currentFrame % 30 == 0 {
                    print("[QRScanner] No QR/all-format barcode detected at frame #\(currentFrame)")
                    showStatus("Scanning QR...\nFrames: \(currentFrame)\nNo QR or barcode detected")
                }
                return
            }

            let status = statusText(for: allBarcodes)
            print("[QRScanner] QR-only missed, all-format detected \(allBarcodes.count) barcode(s) at frame #\(currentFrame): \(status.replacingOccurrences(of: "\n", with: " | "))")
            showStatus("QR-only missed; all-format saw:\n\(status)")
        } catch {
            isProcessingFrame = false
            print("[QRScanner] Synchronous scan failed on frame #\(currentFrame): \(error.localizedDescription)")
            showStatus("Scan failed: \(error.localizedDescription)")
        }
    }
}
