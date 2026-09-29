/**
 * src/renderer/components/labeling/CanvasContainer.tsx
 * Modular container managing the 3-layer canvas stack.
 */

import React from 'react';
import { LabelingCanvas } from './LabelingCanvas';

export const CanvasContainer: React.FC = () => {
  return <LabelingCanvas />;
};

export default CanvasContainer;
