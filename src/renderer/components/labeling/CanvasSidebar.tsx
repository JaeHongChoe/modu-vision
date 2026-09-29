/**
 * src/renderer/components/labeling/CanvasSidebar.tsx
 * Right-hand labeling sidebar integrating annotation listing and inspector actions.
 */

import React from 'react';
import { AnnotationList } from './AnnotationList';

export const CanvasSidebar: React.FC = () => {
  return <AnnotationList />;
};

export default CanvasSidebar;
