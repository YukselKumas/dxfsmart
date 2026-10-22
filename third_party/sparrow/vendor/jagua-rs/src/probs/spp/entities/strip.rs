use crate::collision_detection::CDEConfig;
use crate::entities::{Container, InferiorQualityZone};
use crate::geometry::primitives::{Rect, SPolygon};
use crate::geometry::shape_modification::{ShapeModifyConfig, ShapeModifyMode};
use crate::geometry::{DTransformation, OriginalShape};
use anyhow::{Result, ensure};

#[derive(Clone, Debug, Copy, PartialEq)]
/// Represents a rectangular container with fixed height and variable width.
pub struct Strip {
    pub fixed_height: f32,
    pub cde_config: CDEConfig,
    pub shape_modify_config: ShapeModifyConfig,
    pub width: f32,
    /// DXFsmart: multi-sheet strip. When `sheet_period > 0`, a forbidden zone
    /// (quality 0 = hole) of width `barrier_width` starts at
    /// `k * sheet_period + barrier_offset` for every k, across the full height.
    /// Items can never cross a sheet boundary, so shrinking the strip width
    /// minimises the length used on the last sheet (and the number of sheets).
    pub sheet_period: f32,
    pub barrier_offset: f32,
    pub barrier_width: f32,
}

impl Strip {
    pub fn new(
        fixed_height: f32,
        cde_config: CDEConfig,
        shape_modify_config: ShapeModifyConfig,
        width: f32,
    ) -> Result<Self> {
        ensure!(fixed_height > 0.0, "strip height must be positive");
        ensure!(width > 0.0, "strip width must be positive");
        Ok(Strip {
            fixed_height,
            cde_config,
            shape_modify_config,
            width,
            sheet_period: 0.0,
            barrier_offset: 0.0,
            barrier_width: 0.0,
        })
    }

    pub fn set_width(&mut self, width: f32) {
        assert!(width > 0.0, "strip width must be positive");
        self.width = width;
    }
}

impl TryFrom<Strip> for Container {
    type Error = anyhow::Error;

    fn try_from(s: Strip) -> Result<Container> {
        let mut zones = vec![];
        if s.sheet_period > 0.0 && s.barrier_width > 0.0 {
            let mut barriers = vec![];
            let mut k = 0usize;
            loop {
                let x0 = k as f32 * s.sheet_period + s.barrier_offset;
                if x0 >= s.width {
                    break;
                }
                let x1 = (x0 + s.barrier_width).min(s.width);
                // A sliver cut off by the strip end is skipped: items keep the
                // separation distance from the strip end anyway, and a sliver
                // this thin breaks the surrogate (pole) generation.
                if x1 - x0 >= s.barrier_width.min(1.0) {
                    barriers.push(OriginalShape {
                        shape: SPolygon::from(Rect::try_new(x0, -1.0, x1, s.fixed_height + 1.0)?),
                        pre_transform: DTransformation::empty(),
                        modify_mode: ShapeModifyMode::Inflate,
                        modify_config: ShapeModifyConfig { offset: None, simplify_tolerance: None, narrow_concavity_cutoff: None },
                    });
                }
                k += 1;
            }
            if !barriers.is_empty() {
                zones.push(InferiorQualityZone::new(0, barriers, s.cde_config.item_surrogate_config)?);
            }
        }
        Container::new(
            OriginalShape {
                shape: SPolygon::from(Rect::try_new(0.0, 0.0, s.width, s.fixed_height)?),
                pre_transform: DTransformation::empty(),
                modify_mode: ShapeModifyMode::Deflate,
                modify_config: s.shape_modify_config,
            },
            zones,
            s.cde_config,
        )
    }
}
