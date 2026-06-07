"""
JARVIS Face Detection
Real-time face detection using OpenCV DNN.
Integrates with v3 tools/ ecosystem.
"""
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

JARVIS_DIR = Path("/home/kali/.jarvis")
MODEL_DIR = JARVIS_DIR / "models" / "face" / "opencv"
PROTOTXT_PATH = MODEL_DIR / "deploy.prototxt"
CAFFEMODEL_PATH = MODEL_DIR / "res10_300x300_ssd_iter_140000.caffemodel"


class FaceDetector:
    """Face detector using OpenCV DNN."""

    def __init__(self, confidence_threshold: float = 0.5):
        self.confidence_threshold = confidence_threshold
        self._net = None

    def _load_model(self):
        if self._net is None:
            if not PROTOTXT_PATH.exists() or not CAFFEMODEL_PATH.exists():
                raise FileNotFoundError(
                    f"Face detection models not found. Expected:\n"
                    f"  {PROTOTXT_PATH}\n"
                    f"  {CAFFEMODEL_PATH}"
                )
            self._net = cv2.dnn.readNetFromCaffe(str(PROTOTXT_PATH), str(CAFFEMODEL_PATH))
        return self._net

    def detect(self, image: np.ndarray) -> List[Dict[str, Any]]:
        """Detect faces in an image.

        Args:
            image: BGR image as numpy array (H, W, 3)

        Returns:
            List of face detections with bbox and confidence
        """
        net = self._load_model()
        h, w = image.shape[:2]

        blob = cv2.dnn.blobFromImage(image, 1.0, (300, 300), [104.0, 177.0, 123.0], False, False)
        net.setInput(blob)
        detections = net.forward()

        faces = []
        for i in range(detections.shape[2]):
            confidence = detections[0, 0, i, 2]
            if confidence > self.confidence_threshold:
                x1 = int(detections[0, 0, i, 3] * w)
                y1 = int(detections[0, 0, i, 4] * h)
                x2 = int(detections[0, 0, i, 5] * w)
                y2 = int(detections[0, 0, i, 6] * h)

                faces.append({
                    "bbox": {
                        "x": max(0, x1),
                        "y": max(0, y1),
                        "width": min(w, x2) - max(0, x1),
                        "height": min(h, y2) - max(0, y1)
                    },
                    "confidence": float(confidence)
                })

        return faces

    def detect_file(self, image_path: str) -> Dict[str, Any]:
        """Detect faces in an image file.

        Args:
            image_path: Path to image file

        Returns:
            Dict with faces count and detections
        """
        image = cv2.imread(image_path)
        if image is None:
            return {"status": "error", "error": f"Could not load image: {image_path}"}

        faces = self.detect(image)
        return {
            "status": "success",
            "face_count": len(faces),
            "faces": faces,
            "image_shape": image.shape[:2]
        }

    def draw_boxes(self, image: np.ndarray, faces: List[Dict[str, Any]], color: Tuple[int, int, int] = (0, 255, 0)) -> np.ndarray:
        """Draw bounding boxes on image."""
        result = image.copy()
        for face in faces:
            bbox = face["bbox"]
            x, y = bbox["x"], bbox["y"]
            w, h = bbox["width"], bbox["height"]
            cv2.rectangle(result, (x, y), (x + w, y + h), color, 2)
            label = f"{face['confidence']:.2f}"
            cv2.putText(result, label, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        return result


def jarvis_detect_faces(image_path: str, confidence: float = 0.5) -> Dict[str, Any]:
    """Tool wrapper for JARVIS tool registry."""
    detector = FaceDetector(confidence_threshold=confidence)
    return detector.detect_file(image_path)


if __name__ == "__main__":
    detector = FaceDetector()
    print("Face Detector initialized")
    print(f"Model: {CAFFEMODEL_PATH.name}")
    print(f"Config: {PROTOTXT_PATH.name}")
