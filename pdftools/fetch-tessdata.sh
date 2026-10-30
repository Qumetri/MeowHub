#!/usr/bin/env bash
# Fetch Tesseract OCR languages for the PDF tools engine into DIR, plus the support files the
# engine image ships (pdf.ttf, configs/) — the directory is mounted OVER the image's tessdata,
# and without pdf.ttf OCRmyPDF fails with "Tesseract ... did not produce the expected PDF output".
#   usage: fetch-tessdata.sh DIR "eng rus fin"
set -euo pipefail
dir=$1; langs="osd ${2:-eng}"
image=${PDFTOOLS_IMAGE:-docker.stirlingpdf.com/stirlingtools/stirling-pdf:3.2.2}
mkdir -p "$dir"
for l in $langs; do
  [[ $l =~ ^[a-z_]+$ ]] || { echo "bad language code: $l" >&2; exit 1; }
  [[ -s $dir/$l.traineddata ]] && continue
  curl -fsSL -o "$dir/$l.traineddata.part" "https://github.com/tesseract-ocr/tessdata/raw/4.1.0/$l.traineddata"
  mv "$dir/$l.traineddata.part" "$dir/$l.traineddata"
done
if [[ ! -f $dir/pdf.ttf ]]; then
  docker run --rm --entrypoint sh "$image" -c \
    'cd /usr/share/tesseract-ocr/5/tessdata && tar -c --exclude="*.traineddata" .' | tar -x -C "$dir"
fi
