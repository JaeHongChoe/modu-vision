/**
 * src/renderer/components/labeling/LabelingCanvas.tsx
 * 3-Layer Interactive Canvas:
 *   - Layer 1 (Base Raster): Original inspection image rendered at 60fps
 *   - Layer 2 (Mask/Heatmap Raster): Defect mask overlay with alpha slider
 *   - Layer 3 (Vector UI): BBoxes with 8 handles, Polygons, and Brush drawing
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { AnnotationItem, BBox, HandleType, Point } from '../../types';
import { useAnnotationStore } from '../../stores/useAnnotationStore';
import { useProjectStore } from '../../stores/useProjectStore';
import { useFoundationPromptStore } from '../../stores/useFoundationPromptStore';
import { brushEditTarget } from './foundationRequest';
import { resolveApiUrl } from '../../services/api';
import { resolveLabelingShortcut } from './labelingShortcuts';
import {
  calcRotatedCorners,
  calculateFitToScreen,
  calculateSmoothZoomAtPoint,
  calculateZoomAtPoint,
  clampPointToImage,
  clientToViewport,
  getBBoxHandles,
  hitTestBBox,
  hitTestHandle,
  hitTestPolygonVertex,
  hitTestRotatedBBox,
  hitTestRotatedHandle,
  imageToViewport,
  moveBBox,
  pointInPolygon,
  resizeBBoxWithHandle,
  sanitizeBBox,
  viewportToImage,
} from '../../utils/coordinateMath';

export const LabelingCanvas: React.FC = () => {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasBaseRef = useRef<HTMLCanvasElement>(null);
  const canvasMaskRef = useRef<HTMLCanvasElement>(null);
  const canvasVectorRef = useRef<HTMLCanvasElement>(null);
  const offscreenBrushCanvasRef = useRef<HTMLCanvasElement | null>(null);

  const baseImageRef = useRef<HTMLImageElement | null>(null);
  const maskImageRef = useRef<HTMLImageElement | null>(null);
  const otherBrushCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const foundationPrompt = useFoundationPromptStore();

  const {
    currentImage,
    annotations,
    selectedAnnotationId,
    activeTool,
    activeCategory,
    brushRadius,
    viewTransform,
    setImageDimensions,
    maskOpacity,
    maskVisible,
    maskUrl,
    heatmapUrl,
    setViewTransform,
    addAnnotation,
    commitBrushMask,
    updateAnnotation,
    setSelectedAnnotationId,
    deleteSelected,
    undo,
    redo,
    setActiveTool,
    triggerAutoSelect,
  } = useAnnotationStore();
  const { backendPort } = useProjectStore();
  useEffect(()=>{useFoundationPromptStore.getState().bind(currentImage?.file_path||'');},[currentImage?.file_path]);

  // Interaction State
  const [isSpacePressed, setIsSpacePressed] = useState(false);
  const [cursorPos, setCursorPos] = useState<Point | null>(null);
  const [imgDimensions, setImgDimensions] = useState<{ width: number; height: number }>({ width: 512, height: 512 });

  // Ref to track latest image dimensions for synchronous event access
  const imgDimensionsRef = useRef<{ width: number; height: number }>({ width: 512, height: 512 });
  useEffect(() => {
    imgDimensionsRef.current = imgDimensions;
  }, [imgDimensions]);

  const rafMoveRef = useRef<number | null>(null);
  const pendingVpPtRef = useRef<Point | null>(null);

  // Dragging & Creation refs
  const dragModeRef = useRef<
    'pan' | 'create_bbox' | 'resize_bbox' | 'move_bbox' | 'move_polygon' | 'rotate_bbox' | 'move_vertex' | 'brush' | null
  >(null);
  const dragStartViewRef = useRef<Point>({ x: 0, y: 0 });
  const dragStartImgRef = useRef<Point>({ x: 0, y: 0 });
  const activeHandleRef = useRef<HandleType | null>(null);
  const activeBBoxSnapshotRef = useRef<BBox | null>(null);
  const activeRotatedBBoxRef = useRef<{ cx: number; cy: number; w: number; h: number; angle: number } | null>(null);
  const activeVertexIndexRef = useRef<number | null>(null);

  // In-progress Polygon
  const polygonPointsRef = useRef<Point[]>([]);
  const [polygonDraft, setPolygonDraft] = useState<Point[]>([]);

  // In-progress BBox creation
  const bboxDraftRef = useRef<{ p1: Point; p2: Point } | null>(null);
  const [bboxDraft, setBBoxDraft] = useState<BBox | null>(null);

  // -------------------------------------------------------------
  // RENDER LAYER 1: Base Inspection Image Raster
  // -------------------------------------------------------------
  const redrawLayer1 = useCallback(() => {
    const cv = canvasBaseRef.current;
    if (!cv || !containerRef.current) return;
    const ctx = cv.getContext('2d');
    if (!ctx) return;

    const rect = containerRef.current.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);

    const img = baseImageRef.current;
    if (!img) return;

    const { scale, offsetX, offsetY } = viewTransform;
    const imgW = imgDimensions.width;
    const imgH = imgDimensions.height;

    // Viewport Frustum Bounded Blit:
    // Only blit the image rectangle visible within the viewport to prevent Metal 16k GPU texture overflow
    const imgX0 = Math.max(0, -offsetX / scale);
    const imgY0 = Math.max(0, -offsetY / scale);
    const imgX1 = Math.min(imgW, (rect.width - offsetX) / scale);
    const imgY1 = Math.min(imgH, (rect.height - offsetY) / scale);

    if (imgX1 <= imgX0 || imgY1 <= imgY0) return;

    const sx = imgX0;
    const sy = imgY0;
    const sw = imgX1 - imgX0;
    const sh = imgY1 - imgY0;

    const dx = offsetX + sx * scale;
    const dy = offsetY + sy * scale;
    const dw = sw * scale;
    const dh = sh * scale;

    ctx.save();
    // Pixelate crisply when zoomed in close (>= 3x) for subpixel defect inspection
    ctx.imageSmoothingEnabled = scale < 3.0;
    ctx.drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh);
    ctx.restore();
  }, [viewTransform, imgDimensions]);

  // -------------------------------------------------------------
  // RENDER LAYER 2: Mask & Heatmap Raster with Opacity
  // -------------------------------------------------------------
  const redrawLayer2 = useCallback(() => {
    const cv = canvasMaskRef.current;
    if (!cv || !containerRef.current) return;
    const ctx = cv.getContext('2d');
    if (!ctx) return;

    const rect = containerRef.current.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);

    if (!maskVisible) return;

    const { scale, offsetX, offsetY } = viewTransform;
    const imgW = imgDimensions.width;
    const imgH = imgDimensions.height;

    const imgX0 = Math.max(0, -offsetX / scale);
    const imgY0 = Math.max(0, -offsetY / scale);
    const imgX1 = Math.min(imgW, (rect.width - offsetX) / scale);
    const imgY1 = Math.min(imgH, (rect.height - offsetY) / scale);

    if (imgX1 <= imgX0 || imgY1 <= imgY0) return;

    const sx = imgX0;
    const sy = imgY0;
    const sw = imgX1 - imgX0;
    const sh = imgY1 - imgY0;

    const dx = offsetX + sx * scale;
    const dy = offsetY + sy * scale;
    const dw = sw * scale;
    const dh = sh * scale;

    ctx.save();
    ctx.globalAlpha = maskOpacity;
    ctx.imageSmoothingEnabled = scale < 3.0;

    // Blit loaded mask/heatmap image
    if (maskImageRef.current) {
      ctx.drawImage(maskImageRef.current, sx, sy, sw, sh, dx, dy, dw, dh);
    }

    if (otherBrushCanvasRef.current) ctx.drawImage(otherBrushCanvasRef.current, sx, sy, sw, sh, dx, dy, dw, dh);

    // Blit offscreen brush canvas
    if (offscreenBrushCanvasRef.current) {
      ctx.drawImage(offscreenBrushCanvasRef.current, sx, sy, sw, sh, dx, dy, dw, dh);
    }

    ctx.restore();
  }, [viewTransform, imgDimensions, maskOpacity, maskVisible]);

  const redrawMaskRef=useRef(redrawLayer2);
  useEffect(()=>{redrawMaskRef.current=redrawLayer2;},[redrawLayer2]);

  // -------------------------------------------------------------
  // RENDER LAYER 3: Vector UI (BBoxes, 8 Handles, Polygons, Cursor)
  // -------------------------------------------------------------
  const redrawLayer3 = useCallback(() => {
    const cv = canvasVectorRef.current;
    if (!cv || !containerRef.current) return;
    const ctx = cv.getContext('2d');
    if (!ctx) return;

    const rect = containerRef.current.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);

    // 1. Render Saved Annotations
    annotations.forEach((ann) => {
      const isSelected = ann.id === selectedAnnotationId;
      const color = ann.color || '#3b82f6';

      // Render BBox
      if (ann.type === 'bbox' && ann.bbox) {
        const [xmin, ymin, xmax, ymax] = ann.bbox;
        const p1 = imageToViewport({ x: xmin, y: ymin }, viewTransform);
        const p2 = imageToViewport({ x: xmax, y: ymax }, viewTransform);
        const bw = p2.x - p1.x;
        const bh = p2.y - p1.y;

        // Fill & Stroke
        ctx.fillStyle = `${color}22`; // semi-transparent
        ctx.fillRect(p1.x, p1.y, bw, bh);

        ctx.strokeStyle = color;
        ctx.lineWidth = isSelected ? 2.5 : 1.5;
        if (isSelected) {
          ctx.setLineDash([4, 2]);
        } else {
          ctx.setLineDash([]);
        }
        ctx.strokeRect(p1.x, p1.y, bw, bh);
        ctx.setLineDash([]);

        // Label Badge
        ctx.fillStyle = color;
        const labelText = `${ann.label}`;
        ctx.font = '11px sans-serif';
        const textWidth = ctx.measureText(labelText).width;
        ctx.fillRect(p1.x, Math.max(0, p1.y - 18), textWidth + 8, 18);
        ctx.fillStyle = '#ffffff';
        ctx.fillText(labelText, p1.x + 4, Math.max(12, p1.y - 5));

        // 8 Handles if Selected
        if (isSelected) {
          const handles = getBBoxHandles({ xmin, ymin, xmax, ymax }, viewTransform);
          handles.forEach((h) => {
            ctx.fillStyle = '#ffffff';
            ctx.strokeStyle = color;
            ctx.lineWidth = 2;
            ctx.fillRect(h.x - 4, h.y - 4, 8, 8);
            ctx.strokeRect(h.x - 4, h.y - 4, 8, 8);
          });
        }
      }

      // Render Rotated BBox (OBB)
      if (ann.type === 'rotated_bbox') {
        const [cx, cy, w, h, angle] = ann.rotated_bbox || [
          ann.bbox ? (ann.bbox[0] + ann.bbox[2]) / 2 : 100,
          ann.bbox ? (ann.bbox[1] + ann.bbox[3]) / 2 : 100,
          ann.bbox ? ann.bbox[2] - ann.bbox[0] : 60,
          ann.bbox ? ann.bbox[3] - ann.bbox[1] : 40,
          0,
        ];
        const vCenter = imageToViewport({ x: cx, y: cy }, viewTransform);
        const vw = w * viewTransform.scale;
        const vh = h * viewTransform.scale;
        const rad = ((angle || 0) * Math.PI) / 180;

        ctx.save();
        ctx.translate(vCenter.x, vCenter.y);
        ctx.rotate(rad);

        ctx.fillStyle = `${color}25`;
        ctx.fillRect(-vw / 2, -vh / 2, vw, vh);

        ctx.strokeStyle = color;
        ctx.lineWidth = isSelected ? 2.5 : 1.5;
        ctx.strokeRect(-vw / 2, -vh / 2, vw, vh);

        // Rotation stem and knob if selected
        if (isSelected) {
          ctx.beginPath();
          ctx.moveTo(0, -vh / 2);
          ctx.lineTo(0, -vh / 2 - 20);
          ctx.strokeStyle = '#06b6d4';
          ctx.lineWidth = 1.5;
          ctx.stroke();

          ctx.beginPath();
          ctx.arc(0, -vh / 2 - 20, 5, 0, Math.PI * 2);
          ctx.fillStyle = '#06b6d4';
          ctx.fill();
          ctx.stroke();

          ctx.font = '10px monospace';
          ctx.fillStyle = '#38bdf8';
          ctx.fillText(`${Math.round(angle || 0)}°`, 8, -vh / 2 - 15);
        }

        ctx.restore();
      }

      // Render Polygon
      const polyPts = ann.polygon || ann.points;
      if (ann.type === 'polygon' && polyPts && polyPts.length >= 3) {
        ctx.beginPath();
        polyPts.forEach(([px, py], i) => {
          const vp = imageToViewport({ x: px, y: py }, viewTransform);
          if (i === 0) ctx.moveTo(vp.x, vp.y);
          else ctx.lineTo(vp.x, vp.y);
        });
        ctx.closePath();

        ctx.fillStyle = `${color}33`; // 20% alpha
        ctx.fill();

        ctx.strokeStyle = color;
        ctx.lineWidth = isSelected ? 2.5 : 1.5;
        ctx.stroke();

        // Polygon Label Badge
        const minX = Math.min(...polyPts.map((p) => p[0]));
        const minY = Math.min(...polyPts.map((p) => p[1]));
        const badgeVp = imageToViewport({ x: minX, y: minY }, viewTransform);
        ctx.fillStyle = color;
        const labelText = `${ann.label}`;
        ctx.font = '11px sans-serif';
        const textWidth = ctx.measureText(labelText).width;
        ctx.fillRect(badgeVp.x, Math.max(0, badgeVp.y - 18), textWidth + 8, 18);
        ctx.fillStyle = '#ffffff';
        ctx.fillText(labelText, badgeVp.x + 4, Math.max(12, badgeVp.y - 5));

        // Render vertices if selected
        if (isSelected) {
          polyPts.forEach(([px, py]) => {
            const vp = imageToViewport({ x: px, y: py }, viewTransform);
            ctx.beginPath();
            ctx.arc(vp.x, vp.y, 4, 0, Math.PI * 2);
            ctx.fillStyle = '#ffffff';
            ctx.fill();
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            ctx.stroke();
          });
        }
      }
    });

    // 2. Render In-progress BBox Draft
    if (bboxDraft) {
      const p1 = imageToViewport({ x: bboxDraft.xmin, y: bboxDraft.ymin }, viewTransform);
      const p2 = imageToViewport({ x: bboxDraft.xmax, y: bboxDraft.ymax }, viewTransform);
      const bw = p2.x - p1.x;
      const bh = p2.y - p1.y;

      ctx.fillStyle = `${activeCategory.color}25`;
      ctx.fillRect(p1.x, p1.y, bw, bh);

      ctx.strokeStyle = activeCategory.color;
      ctx.lineWidth = 2;
      ctx.setLineDash([4, 2]);
      ctx.strokeRect(p1.x, p1.y, bw, bh);
      ctx.setLineDash([]);

      // Dimension indicator: e.g. "120 x 85 px"
      const wPx = Math.round(bboxDraft.xmax - bboxDraft.xmin);
      const hPx = Math.round(bboxDraft.ymax - bboxDraft.ymin);
      const dimText = `${wPx} × ${hPx} px`;
      ctx.font = '10px monospace';
      const tw = ctx.measureText(dimText).width;
      ctx.fillStyle = 'rgba(11, 14, 20, 0.9)';
      ctx.fillRect(p2.x - tw - 6, p2.y + 4, tw + 6, 16);
      ctx.strokeStyle = '#2B3547';
      ctx.lineWidth = 1;
      ctx.strokeRect(p2.x - tw - 6, p2.y + 4, tw + 6, 16);
      ctx.fillStyle = '#E2E8F0';
      ctx.fillText(dimText, p2.x - tw - 3, p2.y + 16);
    }

    // 3. Render In-progress Polygon Draft
    if (polygonDraft.length > 0) {
      ctx.beginPath();
      polygonDraft.forEach((pt, i) => {
        const vp = imageToViewport(pt, viewTransform);
        if (i === 0) ctx.moveTo(vp.x, vp.y);
        else ctx.lineTo(vp.x, vp.y);
      });

      if (cursorPos) {
        ctx.lineTo(cursorPos.x, cursorPos.y);
      }

      ctx.strokeStyle = activeCategory.color;
      ctx.lineWidth = 2;
      ctx.stroke();

      // Vertex dots
      polygonDraft.forEach((pt, i) => {
        const vp = imageToViewport(pt, viewTransform);
        ctx.beginPath();
        ctx.arc(vp.x, vp.y, 4, 0, Math.PI * 2);
        ctx.fillStyle = i === 0 ? '#10b981' : '#ffffff';
        ctx.fill();
        ctx.strokeStyle = activeCategory.color;
        ctx.stroke();
      });

      // Snap indicator to vertex 0
      if (cursorPos && polygonDraft.length >= 2) {
        const v0 = imageToViewport(polygonDraft[0], viewTransform);
        const dist = Math.hypot(cursorPos.x - v0.x, cursorPos.y - v0.y);
        if (dist <= 14) {
          ctx.beginPath();
          ctx.arc(v0.x, v0.y, 10, 0, Math.PI * 2);
          ctx.strokeStyle = '#10b981';
          ctx.lineWidth = 2.5;
          ctx.setLineDash([3, 2]);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    }

    for (const point of foundationPrompt.points) {
      const vp=imageToViewport(point,viewTransform);ctx.beginPath();ctx.arc(vp.x,vp.y,5,0,Math.PI*2);
      ctx.fillStyle=point.label===1?'#22d3ee':'#ef4444';ctx.fill();ctx.strokeStyle='#ffffff';ctx.stroke();
    }
    for(const box of foundationPrompt.boxes){const a=imageToViewport({x:box[0],y:box[1]},viewTransform);const b=imageToViewport({x:box[2],y:box[3]},viewTransform);ctx.strokeStyle='#22d3ee';ctx.lineWidth=2;ctx.strokeRect(a.x,a.y,b.x-a.x,b.y-a.y);}
    // 4. Brush Cursor Diameter Preview
    if (cursorPos && (activeTool === 'brush' || activeTool === 'eraser')) {
      const radiusVp = brushRadius * viewTransform.scale;
      ctx.beginPath();
      ctx.arc(cursorPos.x, cursorPos.y, radiusVp, 0, Math.PI * 2);
      ctx.strokeStyle = activeTool === 'eraser' ? '#ef4444' : activeCategory.color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([3, 2]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }, [
    annotations,
    selectedAnnotationId,
    bboxDraft,
    polygonDraft,
    cursorPos,
    activeTool,
    activeCategory,
    brushRadius,
    viewTransform,
    foundationPrompt.points,
    foundationPrompt.boxes,
  ]);

  const redrawAllLayers = useCallback(() => {
    redrawLayer1();
    redrawLayer2();
    redrawLayer3();
  }, [redrawLayer1, redrawLayer2, redrawLayer3]);

  // -------------------------------------------------------------
  // Canvas Size Synchronization (High-DPI / Retina)
  // -------------------------------------------------------------
  const resizeCanvases = useCallback(() => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;

    [canvasBaseRef.current, canvasMaskRef.current, canvasVectorRef.current].forEach((cv) => {
      if (cv) {
        cv.width = rect.width * dpr;
        cv.height = rect.height * dpr;
        cv.style.width = `${rect.width}px`;
        cv.style.height = `${rect.height}px`;
        const ctx = cv.getContext('2d');
        if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
    });

    redrawAllLayers();
  }, [redrawAllLayers]);

  // -------------------------------------------------------------
  // Load Base Inspection Image
  // -------------------------------------------------------------
  useEffect(() => {
    if (!currentImage) return;

    const img = new Image();
    const encodedPath = encodeURIComponent(currentImage.file_path || '');
    const encodedName = encodeURIComponent(currentImage.file_name || 'image.png');
    // Prefer uncompressed raw image for pixel-accurate machine vision inspection
    const rawUrl = resolveApiUrl(foundationPrompt.displaySourcePath === currentImage.file_path && foundationPrompt.displaySource ? foundationPrompt.displaySource : `/api/dataset/raw/${encodedName}?file_path=${encodedPath}`);
    const thumbUrl = resolveApiUrl(`/api/dataset/thumbnail/${encodedName}?file_path=${encodedPath}&size=1024`);

    img.crossOrigin = 'anonymous';

    img.onload = () => {
      baseImageRef.current = img;
      const w = img.naturalWidth || 512;
      const h = img.naturalHeight || 512;
      setImgDimensions({ width: w, height: h });
      setImageDimensions({ width: w, height: h });

      // Create or resize offscreen brush canvas
      if (!offscreenBrushCanvasRef.current) {
        offscreenBrushCanvasRef.current = document.createElement('canvas');
      }
      offscreenBrushCanvasRef.current.width = w;
      offscreenBrushCanvasRef.current.height = h;

      // Fit to container view
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        const initialTransform = calculateFitToScreen(rect.width, rect.height, w, h);
        setViewTransform(initialTransform);
      }
      redrawAllLayers();
    };

    img.onerror = (err) => {
      console.warn(`[LabelingCanvas] Failed to load raw image from ${rawUrl}, trying thumbnail:`, err);
      if (img.src !== thumbUrl) {
        img.src = thumbUrl;
      }
    };

    img.src = rawUrl;
  // Redraw callbacks change on every pan/zoom; reloading here would reset the view to Fit.
  }, [currentImage, backendPort, setViewTransform, foundationPrompt.displaySource, foundationPrompt.displaySourcePath]);

  // -------------------------------------------------------------
  // Load Mask / Heatmap Image
  // -------------------------------------------------------------
  useEffect(() => {
    const overlaySrc = heatmapUrl || (annotations.some(a=>a.type==='brush_mask') ? null : maskUrl);
    if (!overlaySrc) {
      maskImageRef.current = null;
      redrawLayer2();
      return;
    }
    const maskImg = new Image();
    maskImg.src = resolveApiUrl(overlaySrc);
    maskImg.crossOrigin = 'anonymous';
    maskImg.onload = () => {
      maskImageRef.current = maskImg;
      redrawLayer2();
    };
  }, [maskUrl, heatmapUrl, annotations, redrawLayer2]);

  const editableBrush = brushEditTarget(annotations,selectedAnnotationId,activeCategory.id);
  const storedBrushMask = editableBrush?.mask_rle;
  useEffect(() => {
    const canvas = offscreenBrushCanvasRef.current;
    if (!canvas || canvas.width !== imgDimensions.width || canvas.height !== imgDimensions.height) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!storedBrushMask) {
      redrawMaskRef.current();
      return;
    }
    let active = true;
    const storedImage = new Image();
    storedImage.onload = () => {
      if (!active) return;
      context.drawImage(storedImage, 0, 0);
      redrawMaskRef.current();
    };
    storedImage.src = storedBrushMask;
    return () => { active = false; };
  }, [storedBrushMask, currentImage?.image_id, imgDimensions.width, imgDimensions.height]);

  useEffect(()=>{
    const other=document.createElement('canvas');other.width=imgDimensions.width;other.height=imgDimensions.height;
    otherBrushCanvasRef.current=other;let active=true;
    const context=other.getContext('2d');
    void (async()=>{for(const annotation of annotations.filter(a=>a.type==='brush_mask'&&a.id!==editableBrush?.id&&a.mask_rle)){
      if(!active)return;const image=await new Promise<HTMLImageElement>((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=reject;image.src=annotation.mask_rle!;});
      if(active&&context)context.drawImage(image,0,0);
    }if(active)redrawMaskRef.current();})().catch(()=>{if(active)redrawMaskRef.current();});
    return()=>{active=false;};
  },[annotations,editableBrush?.id,imgDimensions.width,imgDimensions.height]);

  useEffect(() => {
    resizeCanvases();
    window.addEventListener('resize', resizeCanvases);
    return () => window.removeEventListener('resize', resizeCanvases);
  }, [resizeCanvases]);

  useEffect(() => {
    redrawLayer1();
  }, [redrawLayer1]);

  useEffect(() => {
    redrawLayer2();
  }, [redrawLayer2]);

  useEffect(() => {
    redrawLayer3();
  }, [redrawLayer3, foundationPrompt.points, foundationPrompt.boxes]);

  // -------------------------------------------------------------
  // Mouse Wheel & Trackpad: Smooth Zoom & Pan Centered on Cursor
  // -------------------------------------------------------------
  useEffect(() => {
    const cv = canvasVectorRef.current;
    if (!cv) return;

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = cv.getBoundingClientRect();
      const cursorVp = clientToViewport(e.clientX, e.clientY, rect);

      const isPinchZoom = e.ctrlKey || e.metaKey || e.altKey;
      if (isPinchZoom) {
        // Trackpad pinch-to-zoom or Ctrl/Cmd/Alt+wheel: smooth exponential scaling centered at cursor
        const factor = Math.exp(-e.deltaY * 0.008);
        setViewTransform((prev) => calculateSmoothZoomAtPoint(cursorVp, prev, factor, 0.01, 40.0));
      } else if (Math.abs(e.deltaX) === 0 && (e.deltaMode !== 0 || Math.abs(e.deltaY) >= 100)) {
        // Notched physical mouse wheel step: 1.15x stepped zoom centered at cursor
        const zoomDelta = -Math.sign(e.deltaY);
        setViewTransform((prev) => calculateZoomAtPoint(cursorVp, prev, zoomDelta, 0.01, 40.0));
      } else {
        // Trackpad 2-finger pan or smooth mouse wheel pan
        setViewTransform((prev) => ({
          ...prev,
          offsetX: prev.offsetX - e.deltaX,
          offsetY: prev.offsetY - e.deltaY,
        }));
      }
    };

    cv.addEventListener('wheel', handleWheel, { passive: false });
    return () => cv.removeEventListener('wheel', handleWheel);
  }, [setViewTransform]);

  // -------------------------------------------------------------
  // Finish Polygon Creation
  // -------------------------------------------------------------
  const finishPolygon = useCallback(() => {
    if (polygonPointsRef.current.length < 3) {
      polygonPointsRef.current = [];
      setPolygonDraft([]);
      return;
    }

    const pts: [number, number][] = polygonPointsRef.current.map((p) => [Math.round(p.x), Math.round(p.y)]);
    const newAnn: AnnotationItem = {
      id: `poly_${Date.now()}`,
      type: 'polygon',
      label: activeCategory.name,
      category_id: activeCategory.id,
      polygon: pts,
      points: pts,
      color: activeCategory.color,
    };
    addAnnotation(newAnn);

    polygonPointsRef.current = [];
    setPolygonDraft([]);
  }, [activeCategory, addAnnotation]);

  // -------------------------------------------------------------
  // Keyboard Shortcuts & 1px / 10px Subpixel Coordinate Nudging
  // -------------------------------------------------------------
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Guard against typing inside input, textarea, contenteditable, or ARIA textbox
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.isContentEditable ||
          target.getAttribute('role') === 'textbox')
      ) {
        return;
      }

      const shortcut = resolveLabelingShortcut(e);
      if (shortcut) {
        e.preventDefault();
        if (shortcut.kind === 'tool') {
          setActiveTool(shortcut.tool);
        } else if (shortcut.kind === 'fit') {
          const rect = containerRef.current?.getBoundingClientRect();
          if (rect && baseImageRef.current) {
            const { width, height } = imgDimensionsRef.current;
            setViewTransform(calculateFitToScreen(rect.width, rect.height, width, height));
          }
        } else if (shortcut.kind === 'undo') {
          undo();
        } else {
          redo();
        }
        return;
      }

      // Spacebar for canvas panning
      if (e.code === 'Space' && !e.repeat) {
        setIsSpacePressed(true);
        return;
      }

      // 1px / 10px Subpixel Coordinate Nudging via Arrow Keys
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
        const storeState = useAnnotationStore.getState();
        const selId = storeState.selectedAnnotationId;
        if (!selId) return;

        const targetAnn = storeState.annotations.find((a) => a.id === selId);
        if (!targetAnn) return;

        e.preventDefault();

        // 1px standard micro-nudge, 10px coarse step with Shift modifier
        const step = e.shiftKey ? 10 : 1;
        let dx = 0;
        let dy = 0;
        if (e.key === 'ArrowLeft') dx = -step;
        else if (e.key === 'ArrowRight') dx = step;
        else if (e.key === 'ArrowUp') dy = -step;
        else if (e.key === 'ArrowDown') dy = step;

        const imgW = imgDimensionsRef.current.width;
        const imgH = imgDimensionsRef.current.height;

        // Invalidate active drag snapshots to prevent mouse drag conflicts
        activeBBoxSnapshotRef.current = null;
        activeRotatedBBoxRef.current = null;

        // 1. Standard Axis-Aligned Bounding Box (BBox)
        if (targetAnn.type === 'bbox' && targetAnn.bbox) {
          const [xmin, ymin, xmax, ymax] = targetAnn.bbox;
          const moved = moveBBox(
            { xmin, ymin, xmax, ymax },
            { x: dx, y: dy },
            imgW,
            imgH
          );
          storeState.updateAnnotation(selId, {
            bbox: [moved.xmin, moved.ymin, moved.xmax, moved.ymax],
          });
        }
        // 2. Rotated Bounding Box (OBB)
        else if (targetAnn.type === 'rotated_bbox') {
          const [cx, cy, w, h, angle] = targetAnn.rotated_bbox || [
            targetAnn.bbox ? (targetAnn.bbox[0] + targetAnn.bbox[2]) / 2 : 100,
            targetAnn.bbox ? (targetAnn.bbox[1] + targetAnn.bbox[3]) / 2 : 100,
            targetAnn.bbox ? targetAnn.bbox[2] - targetAnn.bbox[0] : 60,
            targetAnn.bbox ? targetAnn.bbox[3] - targetAnn.bbox[1] : 40,
            0,
          ];
          const newCx = Math.max(0, Math.min(imgW, cx + dx));
          const newCy = Math.max(0, Math.min(imgH, cy + dy));
          const corners = calcRotatedCorners({ x: newCx, y: newCy }, w, h, angle);
          const xs = corners.map((c) => c.x);
          const ys = corners.map((c) => c.y);
          storeState.updateAnnotation(selId, {
            rotated_bbox: [newCx, newCy, w, h, angle],
            bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
          });
        }
        // 3. Polygon Mask
        else if (targetAnn.type === 'polygon') {
          const polyCoords = targetAnn.polygon || targetAnn.points;
          if (polyCoords && polyCoords.length >= 3) {
            const minX = Math.min(...polyCoords.map((p) => p[0]));
            const maxX = Math.max(...polyCoords.map((p) => p[0]));
            const minY = Math.min(...polyCoords.map((p) => p[1]));
            const maxY = Math.max(...polyCoords.map((p) => p[1]));
            const clampedDx = Math.max(-minX, Math.min(imgW - maxX, dx));
            const clampedDy = Math.max(-minY, Math.min(imgH - maxY, dy));
            const updatedPts: [number, number][] = polyCoords.map(([px, py]) => [
              Math.round((px + clampedDx) * 100) / 100,
              Math.round((py + clampedDy) * 100) / 100,
            ]);
            storeState.updateAnnotation(selId, {
              polygon: updatedPts,
              points: updatedPts,
              bbox: [minX + clampedDx, minY + clampedDy, maxX + clampedDx, maxY + clampedDy],
            });
          }
        }
        return;
      }

      // Escape: Cancel current drafting / deselect
      if (e.key === 'Escape') {
        polygonPointsRef.current = [];
        setPolygonDraft([]);
        bboxDraftRef.current = null;
        setBBoxDraft(null);
        dragModeRef.current = null;
        setSelectedAnnotationId(null);
      }
      // Delete / Backspace: Remove selected annotation
      else if (e.key === 'Delete' || e.key === 'Backspace') {
        deleteSelected();
      }
      // Enter: Complete polygon drafting
      else if (e.key === 'Enter') {
        if (polygonPointsRef.current.length >= 3) {
          finishPolygon();
        }
      }
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      if (e.code === 'Space') {
        setIsSpacePressed(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    window.addEventListener('keyup', handleKeyUp);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('keyup', handleKeyUp);
    };
  }, [deleteSelected, undo, redo, setActiveTool, setViewTransform, setSelectedAnnotationId, finishPolygon]);

  // -------------------------------------------------------------
  // Brush Painting to Offscreen Canvas
  // -------------------------------------------------------------
  const paintBrushStroke = useCallback((p1: Point, p2: Point) => {
    const cv = offscreenBrushCanvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext('2d');
    if (!ctx) return;

    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.lineWidth = brushRadius * 2;

    if (activeTool === 'eraser') {
      ctx.globalCompositeOperation = 'destination-out';
    } else {
      ctx.globalCompositeOperation = 'source-over';
      ctx.strokeStyle = activeCategory.color;
    }

    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.stroke();
    ctx.restore();

    redrawLayer2();
  }, [activeTool, brushRadius, activeCategory.color, redrawLayer2]);

  // -------------------------------------------------------------
  // Pointer Events: Down, Move, Up
  // -------------------------------------------------------------
  const handlePointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const vpPt = clientToViewport(e.clientX, e.clientY, rect);
    const imgPt = viewportToImage(vpPt, viewTransform);

    dragStartViewRef.current = vpPt;
    dragStartImgRef.current = imgPt;

    // Pan via Middle Click or Space + Left Click
    if (e.button === 1 || (e.button === 0 && isSpacePressed)) {
      dragModeRef.current = 'pan';
      return;
    }

    if (e.button !== 0) return;

    // 1. Tool: Select / Move
    if (activeTool === 'select') {
      // Check handles/knobs of currently selected annotation first
      if (selectedAnnotationId) {
        const selectedAnn = annotations.find((a) => a.id === selectedAnnotationId);
        if (selectedAnn?.type === 'bbox' && selectedAnn.bbox) {
          const [xmin, ymin, xmax, ymax] = selectedAnn.bbox;
          const hitH = hitTestHandle(vpPt, { xmin, ymin, xmax, ymax }, viewTransform);
          if (hitH) {
            dragModeRef.current = 'resize_bbox';
            activeHandleRef.current = hitH.type;
            activeBBoxSnapshotRef.current = { xmin, ymin, xmax, ymax };
            return;
          }
        } else if (selectedAnn?.type === 'rotated_bbox') {
          const [cx, cy, w, h, angle] = selectedAnn.rotated_bbox || [
            selectedAnn.bbox ? (selectedAnn.bbox[0] + selectedAnn.bbox[2]) / 2 : 100,
            selectedAnn.bbox ? (selectedAnn.bbox[1] + selectedAnn.bbox[3]) / 2 : 100,
            selectedAnn.bbox ? selectedAnn.bbox[2] - selectedAnn.bbox[0] : 60,
            selectedAnn.bbox ? selectedAnn.bbox[3] - selectedAnn.bbox[1] : 40,
            0,
          ];
          const vCenter = imageToViewport({ x: cx, y: cy }, viewTransform);
          const vw = w * viewTransform.scale;
          const vh = h * viewTransform.scale;
          if (hitTestRotatedHandle(vpPt, vCenter, vw, vh, angle || 0, 10)) {
            dragModeRef.current = 'rotate_bbox';
            activeRotatedBBoxRef.current = { cx, cy, w, h, angle: angle || 0 };
            return;
          }
        } else if (selectedAnn?.type === 'polygon') {
          const polyCoords = selectedAnn.polygon || selectedAnn.points;
          if (polyCoords && polyCoords.length >= 3) {
            const pts = polyCoords.map(([x, y]) => ({ x, y }));
            const hitV = hitTestPolygonVertex(vpPt, pts, viewTransform, 8);
            if (hitV !== null) {
              dragModeRef.current = 'move_vertex';
              activeVertexIndexRef.current = hitV;
              return;
            }
            if (pointInPolygon(imgPt, pts)) {
              dragModeRef.current = 'move_polygon';
              dragStartImgRef.current = imgPt;
              return;
            }
          }
        }
      }

      // Hit Test Annotations (Topmost first)
      for (let i = annotations.length - 1; i >= 0; i--) {
        const ann = annotations[i];
        if (ann.type === 'bbox' && ann.bbox) {
          const [xmin, ymin, xmax, ymax] = ann.bbox;
          if (hitTestBBox(imgPt, { xmin, ymin, xmax, ymax })) {
            setSelectedAnnotationId(ann.id || null);
            dragModeRef.current = 'move_bbox';
            activeBBoxSnapshotRef.current = { xmin, ymin, xmax, ymax };
            activeRotatedBBoxRef.current = null;
            return;
          }
        } else if (ann.type === 'rotated_bbox') {
          const [cx, cy, w, h, angle] = ann.rotated_bbox || [
            ann.bbox ? (ann.bbox[0] + ann.bbox[2]) / 2 : 100,
            ann.bbox ? (ann.bbox[1] + ann.bbox[3]) / 2 : 100,
            ann.bbox ? ann.bbox[2] - ann.bbox[0] : 60,
            ann.bbox ? ann.bbox[3] - ann.bbox[1] : 40,
            0,
          ];
          const vCenter = imageToViewport({ x: cx, y: cy }, viewTransform);
          const vw = w * viewTransform.scale;
          const vh = h * viewTransform.scale;
          if (hitTestRotatedHandle(vpPt, vCenter, vw, vh, angle || 0, 10)) {
            setSelectedAnnotationId(ann.id || null);
            dragModeRef.current = 'rotate_bbox';
            activeRotatedBBoxRef.current = { cx, cy, w, h, angle: angle || 0 };
            return;
          }
          if (hitTestRotatedBBox(imgPt, { x: cx, y: cy }, w, h, angle || 0)) {
            setSelectedAnnotationId(ann.id || null);
            dragModeRef.current = 'move_bbox';
            activeBBoxSnapshotRef.current = {
              xmin: cx - w / 2,
              ymin: cy - h / 2,
              xmax: cx + w / 2,
              ymax: cy + h / 2,
            };
            activeRotatedBBoxRef.current = { cx, cy, w, h, angle: angle || 0 };
            return;
          }
        } else if (ann.type === 'polygon') {
          const polyCoords = ann.polygon || ann.points;
          if (polyCoords && polyCoords.length >= 3) {
            const pts = polyCoords.map(([x, y]) => ({ x, y }));
            const hitV = hitTestPolygonVertex(vpPt, pts, viewTransform, 8);
            if (hitV !== null) {
              setSelectedAnnotationId(ann.id || null);
              dragModeRef.current = 'move_vertex';
              activeVertexIndexRef.current = hitV;
              return;
            }
            if (pointInPolygon(imgPt, pts)) {
              setSelectedAnnotationId(ann.id || null);
              dragModeRef.current = 'move_polygon';
              dragStartImgRef.current = imgPt;
              return;
            }
          }
        }
      }

      // Clicked on empty canvas: deselect
      setSelectedAnnotationId(null);
    }

    // 2. Tool: BBox & Rotated BBox Draw
    else if (activeTool === 'bbox' || activeTool === 'rotated_bbox' || activeTool === 'foundation_box') {
      const clamped = clampPointToImage(imgPt, imgDimensions.width, imgDimensions.height);
      dragModeRef.current = 'create_bbox';
      bboxDraftRef.current = { p1: clamped, p2: clamped };
      setBBoxDraft({ xmin: clamped.x, ymin: clamped.y, xmax: clamped.x, ymax: clamped.y });
    }

    // 2.5 Tool: AI Auto-Selector (Smart Magic Wand)
    else if (activeTool === 'foundation_point') {
      const point=clampPointToImage(imgPt,imgDimensions.width,imgDimensions.height);
      useFoundationPromptStore.getState().addPoint({...point,label:useFoundationPromptStore.getState().pointLabel});
    }
    else if (activeTool === 'auto_select') {
      const clamped = clampPointToImage(imgPt, imgDimensions.width, imgDimensions.height);
      triggerAutoSelect(clamped.x, clamped.y);
    }

    // 3. Tool: Polygon Draw
    else if (activeTool === 'polygon') {
      const clamped = clampPointToImage(imgPt, imgDimensions.width, imgDimensions.height);

      // Check snap to start
      if (polygonPointsRef.current.length >= 2) {
        const v0 = imageToViewport(polygonPointsRef.current[0], viewTransform);
        if (Math.hypot(vpPt.x - v0.x, vpPt.y - v0.y) <= 14) {
          finishPolygon();
          return;
        }
      }

      polygonPointsRef.current.push(clamped);
      setPolygonDraft([...polygonPointsRef.current]);
    }

    // 4. Tool: Brush / Eraser Draw
    else if (activeTool === 'brush' || activeTool === 'eraser') {
      dragModeRef.current = 'brush';
      paintBrushStroke(imgPt, imgPt);
    }
  };

  const updateCursorPosThrottled = useCallback((vpPt: Point) => {
    pendingVpPtRef.current = vpPt;
    if (rafMoveRef.current === null) {
      rafMoveRef.current = requestAnimationFrame(() => {
        rafMoveRef.current = null;
        if (pendingVpPtRef.current) {
          setCursorPos(pendingVpPtRef.current);
        }
      });
    }
  }, []);

  useEffect(() => {
    return () => {
      if (rafMoveRef.current !== null) {
        cancelAnimationFrame(rafMoveRef.current);
      }
    };
  }, []);

  const handlePointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const vpPt = clientToViewport(e.clientX, e.clientY, rect);
    const imgPt = viewportToImage(vpPt, viewTransform);
    updateCursorPosThrottled(vpPt);

    const mode = dragModeRef.current;

    // 1. Pan Drag
    if (mode === 'pan') {
      const dx = vpPt.x - dragStartViewRef.current.x;
      const dy = vpPt.y - dragStartViewRef.current.y;
      dragStartViewRef.current = vpPt;
      setViewTransform((prev) => ({
        ...prev,
        offsetX: prev.offsetX + dx,
        offsetY: prev.offsetY + dy,
      }));
      return;
    }

    // 2. Create BBox Drag
    if (mode === 'create_bbox' && bboxDraftRef.current) {
      const clamped = clampPointToImage(imgPt, imgDimensions.width, imgDimensions.height);
      bboxDraftRef.current.p2 = clamped;
      const sanitized = sanitizeBBox(bboxDraftRef.current.p1, clamped, imgDimensions.width, imgDimensions.height, 1);
      if (sanitized) {
        setBBoxDraft(sanitized);
      }
      return;
    }

    // 3. Resize BBox Handle Drag
    if (mode === 'resize_bbox' && activeHandleRef.current && activeBBoxSnapshotRef.current && selectedAnnotationId) {
      const updated = resizeBBoxWithHandle(
        activeBBoxSnapshotRef.current,
        activeHandleRef.current,
        imgPt,
        imgDimensions.width,
        imgDimensions.height
      );
      updateAnnotation(selectedAnnotationId, {
        bbox: [updated.xmin, updated.ymin, updated.xmax, updated.ymax],
      });
      return;
    }

    // 3.5 Rotate BBox Knob Drag
    if (mode === 'rotate_bbox' && activeRotatedBBoxRef.current && selectedAnnotationId) {
      const { cx, cy, w, h } = activeRotatedBBoxRef.current;
      const angleDeg = (Math.atan2(imgPt.y - cy, imgPt.x - cx) * 180) / Math.PI + 90;
      const normalizedAngle = Math.round((((angleDeg % 360) + 540) % 360 - 180) * 10) / 10;
      updateAnnotation(selectedAnnotationId, {
        rotated_bbox: [cx, cy, w, h, normalizedAngle],
      });
      return;
    }

    // 3.6 Move Polygon Vertex Drag
    if (mode === 'move_vertex' && activeVertexIndexRef.current !== null && selectedAnnotationId) {
      const ann = annotations.find((a) => a.id === selectedAnnotationId);
      const polyCoords = ann?.polygon || ann?.points;
      if (polyCoords) {
        const clamped = clampPointToImage(imgPt, imgDimensions.width, imgDimensions.height);
        const updatedPts: [number, number][] = polyCoords.map(([px, py], idx) => {
          if (idx === activeVertexIndexRef.current) {
            return [clamped.x, clamped.y];
          }
          return [px, py];
        });
        const xs = updatedPts.map((p) => p[0]);
        const ys = updatedPts.map((p) => p[1]);
        updateAnnotation(selectedAnnotationId, {
          polygon: updatedPts,
          points: updatedPts,
          bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
        });
        return;
      }
    }

    // 4. Move BBox Drag
    if (mode === 'move_bbox' && selectedAnnotationId) {
      const delta: Point = {
        x: imgPt.x - dragStartImgRef.current.x,
        y: imgPt.y - dragStartImgRef.current.y,
      };

      if (activeRotatedBBoxRef.current) {
        const { cx, cy, w, h, angle } = activeRotatedBBoxRef.current;
        const newCx = Math.max(0, Math.min(imgDimensions.width, cx + delta.x));
        const newCy = Math.max(0, Math.min(imgDimensions.height, cy + delta.y));
        const corners = calcRotatedCorners({ x: newCx, y: newCy }, w, h, angle);
        const xs = corners.map((c) => c.x);
        const ys = corners.map((c) => c.y);
        updateAnnotation(selectedAnnotationId, {
          rotated_bbox: [newCx, newCy, w, h, angle],
          bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
        });
        return;
      }

      if (activeBBoxSnapshotRef.current) {
        const moved = moveBBox(activeBBoxSnapshotRef.current, delta, imgDimensions.width, imgDimensions.height);
        updateAnnotation(selectedAnnotationId, {
          bbox: [moved.xmin, moved.ymin, moved.xmax, moved.ymax],
        });
        return;
      }
    }

    // 4.5 Move Polygon Drag
    if (mode === 'move_polygon' && selectedAnnotationId) {
      const delta: Point = {
        x: imgPt.x - dragStartImgRef.current.x,
        y: imgPt.y - dragStartImgRef.current.y,
      };
      const ann = annotations.find((a) => a.id === selectedAnnotationId);
      const polyCoords = ann?.polygon || ann?.points;
      if (polyCoords && polyCoords.length >= 3) {
        const minX = Math.min(...polyCoords.map((p) => p[0]));
        const maxX = Math.max(...polyCoords.map((p) => p[0]));
        const minY = Math.min(...polyCoords.map((p) => p[1]));
        const maxY = Math.max(...polyCoords.map((p) => p[1]));
        const clampedDx = Math.max(-minX, Math.min(imgDimensions.width - maxX, delta.x));
        const clampedDy = Math.max(-minY, Math.min(imgDimensions.height - maxY, delta.y));
        if (clampedDx !== 0 || clampedDy !== 0) {
          const updatedPts: [number, number][] = polyCoords.map(([px, py]) => [
            Math.round(px + clampedDx),
            Math.round(py + clampedDy),
          ]);
          updateAnnotation(selectedAnnotationId, {
            polygon: updatedPts,
            points: updatedPts,
            bbox: [minX + clampedDx, minY + clampedDy, maxX + clampedDx, maxY + clampedDy],
          });
          dragStartImgRef.current = {
            x: dragStartImgRef.current.x + clampedDx,
            y: dragStartImgRef.current.y + clampedDy,
          };
        }
      }
      return;
    }

    // 5. Brush / Eraser Drag
    if (mode === 'brush') {
      paintBrushStroke(dragStartImgRef.current, imgPt);
      dragStartImgRef.current = imgPt;
    }
  };

  const handlePointerUp = () => {
    const mode = dragModeRef.current;
    if (mode === 'brush' && offscreenBrushCanvasRef.current) {
      commitBrushMask(offscreenBrushCanvasRef.current.toDataURL('image/png'));
    }
    dragModeRef.current = null;
    activeHandleRef.current = null;
    activeBBoxSnapshotRef.current = null;
    activeRotatedBBoxRef.current = null;
    activeVertexIndexRef.current = null;

    // Finalize BBox Creation (1px micro-flaw retention)
    if (mode === 'create_bbox' && bboxDraftRef.current) {
      const valid = sanitizeBBox(
        bboxDraftRef.current.p1,
        bboxDraftRef.current.p2,
        imgDimensions.width,
        imgDimensions.height,
        1
      );
      if (valid && activeTool === 'foundation_box') {
        useFoundationPromptStore.getState().addBox([valid.xmin,valid.ymin,valid.xmax,valid.ymax]);
      } else if (valid) {
        const isRotated = activeTool === 'rotated_bbox';
        const cx = (valid.xmin + valid.xmax) / 2;
        const cy = (valid.ymin + valid.ymax) / 2;
        const bw = valid.xmax - valid.xmin;
        const bh = valid.ymax - valid.ymin;

        const newAnn: AnnotationItem = {
          id: `${isRotated ? 'roto' : 'bbox'}_${Date.now()}`,
          type: isRotated ? 'rotated_bbox' : 'bbox',
          label: activeCategory.name,
          category_id: activeCategory.id,
          bbox: [valid.xmin, valid.ymin, valid.xmax, valid.ymax],
          rotated_bbox: isRotated ? [cx, cy, bw, bh, 0] : undefined,
          color: activeCategory.color,
        };
        addAnnotation(newAnn);
      }
      bboxDraftRef.current = null;
      setBBoxDraft(null);
    }
  };

  const handlePointerLeave = () => {
    handlePointerUp();
    if (rafMoveRef.current !== null) {
      cancelAnimationFrame(rafMoveRef.current);
      rafMoveRef.current = null;
    }
    setCursorPos(null);
  };

  // Determine Dynamic Cursor
  let dynamicCursor = 'crosshair';
  if (isSpacePressed || dragModeRef.current === 'pan') {
    dynamicCursor = 'grab';
  } else if (dragModeRef.current === 'move_bbox' || dragModeRef.current === 'move_polygon') {
    dynamicCursor = 'move';
  } else if (dragModeRef.current === 'rotate_bbox') {
    dynamicCursor = 'crosshair';
  } else if (dragModeRef.current === 'move_vertex') {
    dynamicCursor = 'pointer';
  } else if (activeTool === 'select') {
    dynamicCursor = 'default';
  }

  // Pre-calculate HUD telemetry
  const cursorImg = cursorPos ? viewportToImage(cursorPos, viewTransform) : null;
  const cursorX = cursorImg ? Math.round(cursorImg.x) : 0;
  const cursorY = cursorImg ? Math.round(cursorImg.y) : 0;
  const scalePercent = Math.round(viewTransform.scale * 100);

  const selectedAnn = selectedAnnotationId
    ? annotations.find((a) => a.id === selectedAnnotationId)
    : null;

  let activeMetrics: { w: number; h: number; angle: number } | null = null;
  if (bboxDraft) {
    activeMetrics = {
      w: Math.round(bboxDraft.xmax - bboxDraft.xmin),
      h: Math.round(bboxDraft.ymax - bboxDraft.ymin),
      angle: 0,
    };
  } else if (selectedAnn) {
    if (selectedAnn.type === 'bbox' && selectedAnn.bbox) {
      activeMetrics = {
        w: Math.round(selectedAnn.bbox[2] - selectedAnn.bbox[0]),
        h: Math.round(selectedAnn.bbox[3] - selectedAnn.bbox[1]),
        angle: 0,
      };
    } else if (selectedAnn.type === 'rotated_bbox') {
      const [, , rw, rh, rAngle] = selectedAnn.rotated_bbox || [
        selectedAnn.bbox ? (selectedAnn.bbox[0] + selectedAnn.bbox[2]) / 2 : 100,
        selectedAnn.bbox ? (selectedAnn.bbox[1] + selectedAnn.bbox[3]) / 2 : 100,
        selectedAnn.bbox ? selectedAnn.bbox[2] - selectedAnn.bbox[0] : 60,
        selectedAnn.bbox ? selectedAnn.bbox[3] - selectedAnn.bbox[1] : 40,
        0,
      ];
      activeMetrics = {
        w: Math.round(rw),
        h: Math.round(rh),
        angle: Math.round((rAngle || 0) * 10) / 10,
      };
    } else if (selectedAnn.type === 'polygon') {
      const pts = selectedAnn.polygon || selectedAnn.points;
      if (pts && pts.length > 0) {
        const xs = pts.map((p) => p[0]);
        const ys = pts.map((p) => p[1]);
        activeMetrics = {
          w: Math.round(Math.max(...xs) - Math.min(...xs)),
          h: Math.round(Math.max(...ys) - Math.min(...ys)),
          angle: 0,
        };
      }
    }
  }

  return (
    <div
      ref={containerRef}
      data-canvas-container="true"
      className="relative w-full h-full bg-[#0B0E14] overflow-hidden select-none touch-none"
      style={{ cursor: dynamicCursor }}
    >
      {/* Layer 1: Base Raster */}
      <canvas ref={canvasBaseRef} className="absolute inset-0 pointer-events-none z-10" />

      {/* Layer 2: Mask / Heatmap Raster */}
      <canvas ref={canvasMaskRef} className="absolute inset-0 pointer-events-none z-20" />

      {/* Layer 3: Vector UI & Pointer Interactivity */}
      <canvas
        ref={canvasVectorRef}
        className="absolute inset-0 z-30"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerLeave}
      />

      {/* Precision Instrument Coordinate HUD */}
      <div
        data-testid="canvas-hud"
        className="absolute bottom-2 left-2 z-40 bg-[#0B0E14]/90 border border-[#2B3547] rounded px-3 py-1.5 text-[11px] font-mono text-slate-300 flex items-center gap-3 shadow-md select-none pointer-events-none"
      >
        {/* Crosshair Cursor Coordinates: X, Y */}
        <div className="flex items-center gap-1.5">
          <span className="text-slate-500 font-semibold tracking-wider text-[10px]">POS</span>
          <span className="text-slate-400">X:</span>
          <span className="tabular-nums text-slate-200">{cursorX}</span>
          <span className="text-slate-500 text-[10px]">px</span>
          <span className="text-slate-400 ml-1">Y:</span>
          <span className="tabular-nums text-slate-200">{cursorY}</span>
          <span className="text-slate-500 text-[10px]">px</span>
        </div>

        <div className="w-px h-3 bg-[#2B3547]" />

        {/* Selected or In-Progress Geometry: W, H, Angle */}
        <div className="flex items-center gap-1.5">
          <span className="text-slate-500 font-semibold tracking-wider text-[10px]">DIM</span>
          <span className="text-slate-400">W:</span>
          <span className="tabular-nums text-slate-200">{activeMetrics ? activeMetrics.w : '--'}</span>
          <span className="text-slate-500 text-[10px]">px</span>
          <span className="text-slate-400 ml-1">H:</span>
          <span className="tabular-nums text-slate-200">{activeMetrics ? activeMetrics.h : '--'}</span>
          <span className="text-slate-500 text-[10px]">px</span>
          <span className="text-slate-400 ml-1">angle</span>
          <span className="tabular-nums text-slate-200">{activeMetrics ? activeMetrics.angle : 0}</span>
          <span className="text-slate-500 text-[10px]">°</span>
        </div>

        <div className="w-px h-3 bg-[#2B3547]" />

        {/* Viewport Scale & Sensor Pitch Calibration (μm) */}
        <div className="flex items-center gap-1.5">
          <span className="text-slate-500 font-semibold tracking-wider text-[10px]">VIEW</span>
          <span className="text-slate-400">scale</span>
          <span className="tabular-nums text-slate-200">{scalePercent}</span>
          <span className="text-slate-500 text-[10px]">%</span>
          <span className="text-[#3B82F6] text-[10px] ml-1">(pixel pitch uncalibrated)</span>
        </div>
      </div>
    </div>
  );
};
