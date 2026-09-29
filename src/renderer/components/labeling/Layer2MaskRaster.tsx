/**
 * src/renderer/components/labeling/Layer2MaskRaster.tsx
 * Layer 2 (Mask/Heatmap Raster): Semi-transparent defect mask or anomaly heatmap overlay.
 */

import React from 'react';

export interface Layer2Props {
  canvasRef?: React.RefObject<HTMLCanvasElement>;
  opacity?: number;
  visible?: boolean;
}

export const Layer2MaskRaster: React.FC<Layer2Props> = ({ canvasRef }) => {
  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 pointer-events-none z-20"
      data-layer="2-mask-raster"
    />
  );
};

export default Layer2MaskRaster;
