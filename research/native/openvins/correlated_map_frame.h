// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <vector>
#include <Eigen/Geometry>
#include "state/State.h"
#include "state/StateHelper.h"

// Research component, called only by the single native state owner. This is not
// a map-measurement adapter or a flight output. See docs/native-map-frame.md.
class CorrelatedMapFrame {
 public:
  using Order = std::vector<std::shared_ptr<ov_type::Type>>;
  using Matrix6 = Eigen::Matrix<double, 6, 6>;

  class Snapshot {
   public:
    int dimension() const { return covariance_.rows(); }
    double timestamp_s() const { return timestamp_; }
    const Eigen::MatrixXd &covariance() const { return covariance_; }
    const std::vector<Eigen::MatrixXd> &means() const { return means_; }
   private:
    friend class CorrelatedMapFrame;
    std::shared_ptr<ov_msckf::State> owner_;
    double timestamp_ = 0;
    Order order_;
    std::vector<Eigen::MatrixXd> means_;
    Eigen::MatrixXd covariance_;
  };

  struct Projection {
    double timestamp_s;
    double observation_timestamp_s;
    double map_age_s;
    Eigen::Matrix4d map_T_imu;
    // Right-local SE(3): rotation radians, then translation metres.
    Matrix6 covariance;
    bool global_pose_authority = false;
    bool covariance_calibrated = false;
    bool px4_publication_enabled = false;
  };

  explicit CorrelatedMapFrame(std::shared_ptr<ov_msckf::State> state);
  CorrelatedMapFrame(const CorrelatedMapFrame &) = delete;
  CorrelatedMapFrame &operator=(const CorrelatedMapFrame &) = delete;
  ~CorrelatedMapFrame();

  // Order must partition the complete native error state in covariance order,
  // including passive poses, biases, calibration and landmarks when present.
  Snapshot capture(const Order &order) const;

  // delta_A = regression * delta_native + epsilon, Cov(epsilon)=conditional.
  // epsilon must be independent of the COMPLETE native state at this snapshot.
  // A is local_T_map; its tangent is native PoseJPL, NOT right-local SE(3).
  // No independence or regression is inferred from a pose-window marginal.
  void initialize(const Snapshot &snapshot, const Eigen::Matrix4d &local_T_map,
                  const Eigen::MatrixXd &regression, const Matrix6 &conditional,
                  double observation_timestamp_s, double expires_at_s);
  Projection project();
  void clear();
  bool active() const { return static_cast<bool>(map_); }

  static Eigen::Matrix4d transform(const ov_type::PoseJPL &pose);
  static Eigen::Matrix<double, 6, 12> projection_jacobian(
      const ov_type::PoseJPL &map, const ov_type::PoseJPL &imu);

 private:
  std::shared_ptr<ov_msckf::State> state_;
  std::shared_ptr<ov_type::PoseJPL> map_;
  double observation_time_ = 0;
  double expiry_ = 0;
  double last_time_ = 0;
};
