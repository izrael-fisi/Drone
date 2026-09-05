// SPDX-License-Identifier: GPL-3.0-or-later
#include "correlated_map_frame.h"
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <Eigen/Cholesky>
#include <Eigen/Eigenvalues>

namespace {
void require(bool condition, const char *message) {
  if (!condition) throw std::runtime_error(message);
}

void check_covariance(const Eigen::MatrixXd &p, bool positive) {
  require(p.rows() > 0 && p.rows() == p.cols() && p.allFinite(), "Invalid covariance shape/value");
  const double scale = std::max(1e-12, p.cwiseAbs().maxCoeff());
  require((p - p.transpose()).cwiseAbs().maxCoeff() <= 1e-10 * scale, "Asymmetric covariance");
  Eigen::SelfAdjointEigenSolver<Eigen::MatrixXd> eig(p);
  require(eig.info() == Eigen::Success, "Covariance decomposition failed");
  require(eig.eigenvalues().minCoeff() >= -1e-10 * scale, "Indefinite covariance");
  if (positive)
    require(eig.eigenvalues().minCoeff() > 1e-12 * eig.eigenvalues().maxCoeff()
            && eig.eigenvalues().maxCoeff() > 0, "Singular conditional covariance");
}

void check_transform(const Eigen::Matrix4d &t) {
  require(t.allFinite() && (t.row(3) - Eigen::RowVector4d(0, 0, 0, 1)).norm() < 1e-12,
          "Invalid map transform");
  Eigen::Matrix3d r = t.topLeftCorner<3, 3>();
  require((r.transpose() * r - Eigen::Matrix3d::Identity()).norm() < 1e-10
          && std::abs(r.determinant() - 1) < 1e-10, "Map rotation must be proper orthonormal");
}
}  // namespace

CorrelatedMapFrame::CorrelatedMapFrame(std::shared_ptr<ov_msckf::State> state) : state_(std::move(state)) {
  require(static_cast<bool>(state_), "Missing native state");
}

CorrelatedMapFrame::~CorrelatedMapFrame() { clear(); }

CorrelatedMapFrame::Snapshot CorrelatedMapFrame::capture(const Order &order) const {
  require(!map_, "Clear the existing map state before preparing a new initialization");
  require(std::isfinite(state_->_timestamp) && state_->_timestamp > 0, "Uninitialized native time");
  require(state_->max_covariance_size() <= 4096, "Native initialization state too large");
  Snapshot result;
  result.owner_ = state_;
  result.timestamp_ = state_->_timestamp;
  result.order_ = order;
  int offset = 0;
  for (const auto &v : order) {
    require(v && v->id() == offset && v->size() > 0, "Incomplete or unordered native state");
    require(v->size() <= state_->max_covariance_size() - offset, "Native state order exceeds covariance");
    offset += v->size();
    require(v->value().allFinite(), "Nonfinite native mean");
    result.means_.push_back(v->value());
  }
  require(offset == state_->max_covariance_size(), "Full native state is required, not a pose marginal");
  result.covariance_ = ov_msckf::StateHelper::get_full_covariance(state_);
  check_covariance(result.covariance_, false);
  return result;
}

void CorrelatedMapFrame::initialize(const Snapshot &s, const Eigen::Matrix4d &local_T_map,
                                  const Eigen::MatrixXd &regression, const Matrix6 &conditional,
                                  double observation_timestamp_s, double expires_at_s) {
  require(!map_, "Map frame already initialized");
  require(s.owner_ == state_ && s.timestamp_ == state_->_timestamp, "Stale or foreign native snapshot");
  require(std::isfinite(observation_timestamp_s) && observation_timestamp_s > 0
          && observation_timestamp_s <= s.timestamp_, "Invalid original map observation time");
  require(std::isfinite(expires_at_s) && expires_at_s > s.timestamp_
          && expires_at_s - observation_timestamp_s <= 300, "Invalid map evidence expiry");
  require(regression.rows() == 6 && regression.cols() == s.dimension() && regression.allFinite(),
          "Full native regression is required");
  check_transform(local_T_map);
  check_covariance(conditional, true);
  auto current = capture(s.order_);
  require(current.covariance_.rows() == s.covariance_.rows()
          && (current.covariance_.array() == s.covariance_.array()).all(), "Native covariance changed");
  for (size_t i = 0; i < s.means_.size(); ++i)
    require((current.means_[i].array() == s.means_[i].array()).all(), "Native mean changed");
  const Eigen::MatrixXd cross = s.covariance_ * regression.transpose();
  const Matrix6 marginal = regression * cross + conditional;
  require(cross.allFinite() && marginal.allFinite(), "Initialization covariance overflow");
  check_covariance(marginal, false);

  auto map = std::make_shared<ov_type::PoseJPL>();
  Eigen::Matrix<double, 7, 1> value;
  value.head<4>() = ov_core::rot_2_quat(local_T_map.topLeftCorner<3, 3>().transpose());
  value.tail<3>() = local_T_map.topRightCorner<3, 1>();
  map->set_value(value);
  map->set_fej(value);

  // Whiten once at initialization: H_L=W, H_R=-W*B, R=I. Native augmentation
  // produces P_xA=P_xx B' without updating the existing local marginal.
  Eigen::LLT<Matrix6> chol(conditional);
  require(chol.info() == Eigen::Success, "Conditional whitening failed");
  Matrix6 w = chol.matrixL().solve(Matrix6::Identity());
  Eigen::MatrixXd h = -w * regression;
  require(w.allFinite() && h.allFinite(), "Nonfinite initialization Jacobian");
  const Eigen::MatrixXd whitened_cross = s.covariance_ * h.transpose();
  const Matrix6 whitened_covariance = h * whitened_cross + Matrix6::Identity();
  require(whitened_cross.allFinite() && whitened_covariance.allFinite(),
          "Whitened initialization covariance overflow");
  ov_msckf::StateHelper::initialize_invertible(state_, map, s.order_, h, w,
                                            Matrix6::Identity(), Eigen::VectorXd::Zero(6));
  map_ = std::move(map);
  observation_time_ = observation_timestamp_s;
  expiry_ = expires_at_s;
  last_time_ = s.timestamp_;
}

Eigen::Matrix4d CorrelatedMapFrame::transform(const ov_type::PoseJPL &pose) {
  Eigen::Matrix4d result = Eigen::Matrix4d::Identity();
  result.topLeftCorner<3, 3>() = pose.Rot().transpose();
  result.topRightCorner<3, 1>() = pose.pos();
  return result;
}

Eigen::Matrix<double, 6, 12> CorrelatedMapFrame::projection_jacobian(
    const ov_type::PoseJPL &map, const ov_type::PoseJPL &imu) {
  const Eigen::Matrix3d r = map.Rot() * imu.Rot().transpose();
  const Eigen::Vector3d p = map.Rot() * (imu.pos() - map.pos());
  Eigen::Matrix<double, 6, 12> h = Eigen::Matrix<double, 6, 12>::Zero();
  h.block<3, 3>(0, 0) = -r.transpose();
  h.block<3, 3>(0, 6).setIdentity();
  h.block<3, 3>(3, 0) = r.transpose() * ov_core::skew_x(p);
  h.block<3, 3>(3, 3) = -imu.Rot();
  h.block<3, 3>(3, 9) = imu.Rot();
  return h;
}

CorrelatedMapFrame::Projection CorrelatedMapFrame::project() {
  require(static_cast<bool>(map_), "No map frame initialized");
  try {
    const double time = state_->_timestamp;
    require(std::isfinite(time) && time >= last_time_ && time < expiry_,
            "Native time reset or map evidence expired; map state retired");
    require(state_->_imu->value().allFinite() && map_->value().allFinite()
            && std::abs(state_->_imu->quat().norm() - 1) < 1e-10
            && std::abs(map_->quat().norm() - 1) < 1e-10, "Invalid native pose");
    const auto p = ov_msckf::StateHelper::get_marginal_covariance(state_, {map_, state_->_imu->pose()});
    check_covariance(p, false);
    const auto h = projection_jacobian(*map_, *state_->_imu->pose());
    Projection result;
    result.timestamp_s = time;
    result.observation_timestamp_s = observation_time_;
    result.map_age_s = time - observation_time_;
    result.map_T_imu = transform(*map_).inverse() * transform(*state_->_imu->pose());
    result.covariance = h * p * h.transpose();
    check_transform(result.map_T_imu);
    check_covariance(result.covariance, false);
    last_time_ = time;
    return result;
  } catch (...) {
    clear();
    throw;
  }
}

void CorrelatedMapFrame::clear() {
  if (map_) ov_msckf::StateHelper::marginalize(state_, map_);
  map_.reset();
}
