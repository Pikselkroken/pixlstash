
import os
from pathlib import Path
import cv2
import argparse
from ultralytics import YOLO
import urllib.request

BODY_PART_MODELS = {
    'hand': {
        'model_path': 'yolov8n-hand.pt',  # Download or specify your hand model
        'output_dir': 'tmp/hand_crops',
        'label': 'hand',
    },
    'mouth': {
        'model_path': 'yolov8n-mouth.pt',  # Download or specify your mouth model
        'output_dir': 'tmp/mouth_crops',
        'label': 'mouth',
    },
    'face': {
        'model_path': 'yolov8n-face.pt',  # Download or specify your face model
        'output_dir': 'tmp/face_crops',
        'label': 'face',
    },
}

def main():
    parser = argparse.ArgumentParser(description='Crop body parts (hand/mouth) from images in a folder using YOLOv8.')
    parser.add_argument('--body_part', choices=BODY_PART_MODELS.keys(), default='hand', help='Body part to crop (hand or mouth)')
    parser.add_argument('--input_dir', default='pictures', help='Input image directory')
    parser.add_argument('--output_dir', default=None, help='Output directory for crops (overrides default)')
    parser.add_argument('--model_path', default=None, help='Path to YOLOv8 model (overrides default)')
    args = parser.parse_args()


    config = BODY_PART_MODELS[args.body_part]
    input_dir = args.input_dir
    output_dir = args.output_dir or config['output_dir']
    model_path = args.model_path or config['model_path']
    label = config['label']

    # URLs for pretrained models (replace with actual URLs for your models)
    MODEL_URLS = {
        'yolov8n-hand.pt': 'https://huggingface.co/Bingsu/adetailer/resolve/main/hand_yolov8n.pt',
        'yolov8n-mouth.pt': 'https://example.com/path/to/yolov8n-mouth.pt',
        'yolov8n-face.pt': 'https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8n.pt',
    }

    def download_model_if_needed(path):
        filename = os.path.basename(path)
        if not os.path.exists(path):
            url = MODEL_URLS.get(filename)
            if url:
                print(f"Downloading {filename} from {url}...")
                urllib.request.urlretrieve(url, path)
                print(f"Downloaded {filename}.")
            else:
                raise FileNotFoundError(f"Model file {filename} not found and no download URL is set.")

    os.makedirs(output_dir, exist_ok=True)
    download_model_if_needed(model_path)
    model = YOLO(model_path)

    def bboxes_overlap(box1, box2):
        x1_min, y1_min, x1_max, y1_max = box1
        x2_min, y2_min, x2_max, y2_max = box2
        return not (x1_max < x2_min or x2_max < x1_min or y1_max < y2_min or y2_max < y1_min)

    def merge_bboxes(boxes):
        if not boxes:
            return []
        merged = []
        used = [False] * len(boxes)
        for i, box in enumerate(boxes):
            if used[i]:
                continue
            x_min, y_min, x_max, y_max = box
            group = [box]
            used[i] = True
            for j in range(i+1, len(boxes)):
                if used[j]:
                    continue
                if bboxes_overlap(box, boxes[j]):
                    group.append(boxes[j])
                    used[j] = True
            if len(group) > 1:
                # Merge all overlapping boxes
                x_min = min(b[0] for b in group)
                y_min = min(b[1] for b in group)
                x_max = max(b[2] for b in group)
                y_max = max(b[3] for b in group)
                merged.append((x_min, y_min, x_max, y_max))
            else:
                merged.append(box)
        return merged

    for img_path in Path(input_dir).glob('**/*'):
        if not img_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.bmp', '.webp']:
            continue
        image = cv2.imread(str(img_path))
        if image is None:
            print(f'Failed to read {img_path}')
            continue
        results = model(image)
        boxes = list(results[0].boxes.xyxy.cpu().numpy())
        n = len(boxes)
        if n == 0:
            continue
        if label == 'hand':
            merged_boxes = merge_bboxes(boxes)
            # If any merged box covers more than one original box, save as _hands.png
            if len(merged_boxes) < n:
                # Merge all overlapping hands into one crop
                x_min = min(b[0] for b in boxes)
                y_min = min(b[1] for b in boxes)
                x_max = max(b[2] for b in boxes)
                y_max = max(b[3] for b in boxes)
                crop = image[int(y_min):int(y_max), int(x_min):int(x_max)]
                out_name = f'{img_path.stem}_hands{img_path.suffix}'
                out_path = os.path.join(output_dir, out_name)
                cv2.imwrite(out_path, crop)
                print(f'Saved {out_path}')
            else:
                for i, box in enumerate(merged_boxes):
                    x_min, y_min, x_max, y_max = map(int, box)
                    crop = image[y_min:y_max, x_min:x_max]
                    out_name = f'{img_path.stem}_hand{i+1}{img_path.suffix}'
                    out_path = os.path.join(output_dir, out_name)
                    cv2.imwrite(out_path, crop)
                    print(f'Saved {out_path}')
        else:
            for i, box in enumerate(boxes):
                x_min, y_min, x_max, y_max = map(int, box)
                crop = image[y_min:y_max, x_min:x_max]
                if label == 'mouth':
                    if n == 1:
                        out_name = f'{img_path.stem}_mouth{img_path.suffix}'
                    else:
                        out_name = f'{img_path.stem}_mouth{i+1}{img_path.suffix}'
                elif label == 'face':
                    if n == 1:
                        out_name = f'{img_path.stem}_face{img_path.suffix}'
                    else:
                        out_name = f'{img_path.stem}_face{i+1}{img_path.suffix}'
                else:
                    out_name = f'{img_path.stem}_{label}{i+1}{img_path.suffix}'
                out_path = os.path.join(output_dir, out_name)
                cv2.imwrite(out_path, crop)
                print(f'Saved {out_path}')

if __name__ == '__main__':
    main()
