// SPDX-License-Identifier: GPL-3.0-or-later
// Verify the adapter's covariance convention against the pinned native update.
#include <cassert>
#include <iostream>
#include <Eigen/Geometry>
#include "types/PoseJPL.h"

int main() {
  double max_error = 0.0;
  for (int sample = 0; sample < 12; ++sample) {
    Eigen::Matrix3d R = (Eigen::AngleAxisd(.17*sample, Eigen::Vector3d::UnitX()) *
                        Eigen::AngleAxisd(-.13*sample, Eigen::Vector3d::UnitY()) *
                        Eigen::AngleAxisd(.23*sample, Eigen::Vector3d::UnitZ())).toRotationMatrix();
    ov_type::PoseJPL nominal;
    Eigen::Matrix<double, 7, 1> value;
    value.head<4>() = ov_core::rot_2_quat(R);
    value.tail<3>() << 3.0, -2.0+sample, .4*sample;
    nominal.set_value(value);
    Eigen::Matrix<double, 6, 6> expected = Eigen::Matrix<double, 6, 6>::Zero();
    expected.topLeftCorner<3,3>().setIdentity();
    expected.bottomRightCorner<3,3>() = R;
    for (int axis = 0; axis < 6; ++axis) {
      Eigen::Matrix<double, 6, 1> samples[2];
      for (int sign = 0; sign < 2; ++sign) {
        auto perturbed = std::dynamic_pointer_cast<ov_type::PoseJPL>(nominal.clone());
        Eigen::Matrix<double, 6, 1> dx = Eigen::Matrix<double, 6, 1>::Zero();
        dx(axis) = (sign == 0 ? -1.0 : 1.0)*1e-6;
        perturbed->update(dx);
        Eigen::AngleAxisd rotation(R * perturbed->Rot().transpose());
        samples[sign].head<3>() = rotation.angle()*rotation.axis();
        samples[sign].tail<3>() = R*(perturbed->pos()-nominal.pos());
      }
      auto numeric = (samples[1]-samples[0])/2e-6;
      max_error = std::max(max_error, (numeric-expected.col(axis)).cwiseAbs().maxCoeff());
    }
  }
  std::cout << "Native PoseJPL to right-local SE3 Jacobian max error: " << max_error << '\n';
  return std::isfinite(max_error) && max_error < 1e-7 ? 0 : 1;
}
