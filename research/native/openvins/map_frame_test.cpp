// SPDX-License-Identifier: GPL-3.0-or-later
#include "correlated_map_frame.h"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>

using ov_msckf::State;
using ov_msckf::StateHelper;
using Matrix6 = CorrelatedMapFrame::Matrix6;

namespace {
void require(bool ok, const char *message) {
  if (!ok) throw std::runtime_error(message);
}
template <class F> void rejects(F f) {
  bool rejected = false;
  try { f(); } catch (const std::runtime_error &) { rejected = true; }
  require(rejected, "Invalid map-frame operation accepted");
}
template <class A, class B> void near(const A &a, const B &b, double tolerance, const char *message) {
  require(a.rows() == b.rows() && a.cols() == b.cols()
          && (a - b).cwiseAbs().maxCoeff() < tolerance, message);
}

Eigen::Matrix4d pose(double angle, Eigen::Vector3d p) {
  Eigen::Matrix4d t = Eigen::Matrix4d::Identity();
  t.topLeftCorner<3, 3>() = (Eigen::AngleAxisd(angle, Eigen::Vector3d::UnitX()) *
      Eigen::AngleAxisd(-angle * .7, Eigen::Vector3d::UnitY()) *
      Eigen::AngleAxisd(angle * .4, Eigen::Vector3d::UnitZ())).toRotationMatrix();
  t.topRightCorner<3, 1>() = p;
  return t;
}
void assign(ov_type::PoseJPL &p, const Eigen::Matrix4d &t) {
  Eigen::Matrix<double, 7, 1> v;
  v.head<4>() = ov_core::rot_2_quat(t.topLeftCorner<3, 3>().transpose());
  v.tail<3>() = t.topRightCorner<3, 1>();
  p.set_value(v);
  p.set_fej(v);
}

std::shared_ptr<State> make_state() {
  ov_msckf::StateOptions options;
  auto state = std::make_shared<State>(options);
  Eigen::MatrixXd l = Eigen::MatrixXd::Identity(15, 15) * .15;
  for (int i = 0; i < 15; ++i)
    for (int j = 0; j < i; ++j) l(i, j) = .02 * std::sin(1. + i * 3 + j);
  StateHelper::set_initial_covariance(state, l * l.transpose(), {state->_imu});
  Eigen::VectorXd value = state->_imu->value();
  ov_type::PoseJPL imu;
  assign(imu, pose(-.3, Eigen::Vector3d(2, 1, 5)));
  value.head<7>() = imu.value();
  value.segment<3>(7) << .7, -.4, .1;
  state->_imu->set_value(value);
  state->_imu->set_fej(value);
  state->_timestamp = 1.;
  StateHelper::augment_clone(state, Eigen::Vector3d::Zero());
  state->_timestamp = 2.;
  return state;
}

Eigen::MatrixXd regression(int n) {
  Eigen::MatrixXd b(6, n);
  for (int i = 0; i < 6; ++i)
    for (int j = 0; j < n; ++j) b(i, j) = .15 * std::cos(1. + i * 5 + j);
  b.leftCols<6>() += Matrix6::Identity();
  return b;
}

void jacobians() {
  double maximum = 0;
  for (int i = 0; i < 12; ++i) {
    ov_type::PoseJPL map, imu;
    assign(map, pose(.1 + i * .13, Eigen::Vector3d(3. + i, -2., .4)));
    assign(imu, pose(-.2 - i * .17, Eigen::Vector3d(-1., 2. + i, 6.)));
    Eigen::Matrix4d nominal = CorrelatedMapFrame::transform(map).inverse() * CorrelatedMapFrame::transform(imu);
    auto h = CorrelatedMapFrame::projection_jacobian(map, imu);
    for (int axis = 0; axis < 12; ++axis) {
      Eigen::Matrix<double, 6, 1> errors[2];
      for (int sign = 0; sign < 2; ++sign) {
        auto a = std::dynamic_pointer_cast<ov_type::PoseJPL>(map.clone());
        auto x = std::dynamic_pointer_cast<ov_type::PoseJPL>(imu.clone());
        Eigen::VectorXd d = Eigen::VectorXd::Zero(6);
        d(axis % 6) = (sign ? 1. : -1.) * 1e-6;
        (axis < 6 ? a : x)->update(d);
        Eigen::Matrix4d perturbed = CorrelatedMapFrame::transform(*a).inverse() * CorrelatedMapFrame::transform(*x);
        Eigen::Matrix4d relative = nominal.inverse() * perturbed;
        Eigen::AngleAxisd rotation(relative.topLeftCorner<3, 3>());
        errors[sign].head<3>() = rotation.angle() * rotation.axis();
        errors[sign].tail<3>() = relative.topRightCorner<3, 1>();
      }
      Eigen::Matrix<double, 6, 1> numeric = (errors[1] - errors[0]) / 2e-6;
      maximum = std::max(maximum, (numeric - h.col(axis)).cwiseAbs().maxCoeff());
    }
  }
  require(maximum < 1e-7, "Global projection Jacobian disagrees with native quaternion update");
  std::cout << "Projection Jacobian max error: " << maximum << '\n';
}

void rejects_invalid_initialization() {
  auto state = make_state();
  CorrelatedMapFrame frame(state);
  const CorrelatedMapFrame::Order order{state->_imu, state->_clones_IMU.at(1.)};
  auto s = frame.capture(order);
  auto b = regression(s.dimension());
  Matrix6 q = Matrix6::Identity() * .01;
  Eigen::Matrix4d t = pose(.4, Eigen::Vector3d(4, -3, 1));
  Eigen::MatrixXd original = StateHelper::get_full_covariance(state);
  rejects([&] { frame.project(); });
  rejects([&] { frame.capture({state->_imu}); });
  rejects([&] { frame.capture({order[1], order[0]}); });
  rejects([&] { frame.initialize(s, t, b.leftCols(6), q, 1., 5.); });
  rejects([&] { frame.initialize(s, t, b, Matrix6::Zero(), 1., 5.); });
  rejects([&] { frame.initialize(s, t, b, Matrix6::Identity() * 1e-320, 1., 5.); });
  Matrix6 bad = q;
  bad(0, 0) = -.1;
  rejects([&] { frame.initialize(s, t, b, bad, 1., 5.); });
  bad = q; bad(0, 1) = .001;
  rejects([&] { frame.initialize(s, t, b, bad, 1., 5.); });
  bad = q; bad(2, 2) = std::numeric_limits<double>::quiet_NaN();
  rejects([&] { frame.initialize(s, t, b, bad, 1., 5.); });
  auto invalid_pose = t; invalid_pose(0, 0) = 2.;
  rejects([&] { frame.initialize(s, invalid_pose, b, q, 1., 5.); });
  rejects([&] { frame.initialize(s, t, b, q, 3., 5.); });
  rejects([&] { frame.initialize(s, t, b, q, 1., 2.); });
  rejects([&] { frame.initialize(s, t, b, q, 1., 302.); });
  rejects([&] { frame.initialize(s, t, Eigen::MatrixXd::Constant(6, s.dimension(), 1e300), q, 1., 5.); });
  CorrelatedMapFrame other(make_state());
  rejects([&] { other.initialize(s, t, b, q, 1., 5.); });
  state->_timestamp = 2.1;
  rejects([&] { frame.initialize(s, t, b, q, 1., 5.); });
  state->_timestamp = 2.;
  auto value = state->_imu->value();
  Eigen::VectorXd changed = value; changed(4) += .1;
  state->_imu->set_value(changed);
  rejects([&] { frame.initialize(s, t, b, q, 1., 5.); });
  state->_imu->set_value(value);
  Matrix6 changed_cov = StateHelper::get_marginal_covariance(state, {state->_imu->pose()});
  changed_cov(0, 0) += .01;
  StateHelper::set_initial_covariance(state, changed_cov, {state->_imu->pose()});
  rejects([&] { frame.initialize(s, t, b, q, 1., 5.); });
  StateHelper::set_initial_covariance(state, original, order);
  near(StateHelper::get_full_covariance(state), original, 1e-15, "Rejected initialization changed covariance");
  require(!frame.active(), "Rejected initialization left a map state");
  frame.initialize(s, t, b, q, 1., 5.);
  rejects([&] { frame.initialize(s, t, b, q, 1., 5.); });
  rejects([&] { frame.capture(order); });
  frame.clear();
  near(StateHelper::get_full_covariance(state), original, 1e-14, "Clear changed local covariance");
  frame.initialize(frame.capture(order), t, b, q, 1., 5.);
  state->_timestamp = 5.;
  rejects([&] { frame.project(); });
  require(!frame.active(), "Expired map frame stayed active");
  state->_timestamp = 2.;
  frame.initialize(frame.capture(order), t, b, q, 1., 5.);
  state->_timestamp = 1.9;
  rejects([&] { frame.project(); });
  require(!frame.active(), "Clock-reset map frame stayed active");
  state->_timestamp = 2.;
  frame.initialize(frame.capture(order), t, b, q, 1., 5.);
  auto invalid_value = state->_imu->value();
  invalid_value(0) = std::numeric_limits<double>::quiet_NaN();
  state->_imu->set_value(invalid_value);
  rejects([&] { frame.project(); });
  require(!frame.active(), "Invalid native pose retained map state");
  state->_imu->set_value(value);
  frame.initialize(frame.capture(order), t, b, q, 1., 5.);
  bad = Matrix6::Identity(); bad(0, 0) = -.1;
  StateHelper::set_initial_covariance(state, bad, {state->_imu->pose()});
  rejects([&] { frame.project(); });
  require(!frame.active(), "Invalid native covariance retained map state");
  StateHelper::set_initial_covariance(state, original, order);
  {
    CorrelatedMapFrame scoped(state);
    scoped.initialize(scoped.capture(order), t, b, q, 1., 5.);
  }
  near(StateHelper::get_full_covariance(state), original, 1e-14, "Destructor leaked map state");
}

void continuous_native_updates() {
  auto state = make_state();
  auto local_only = make_state();
  CorrelatedMapFrame frame(state);
  auto snapshot = frame.capture({state->_imu, state->_clones_IMU.at(1.)});
  Eigen::MatrixXd b = regression(snapshot.dimension());
  Matrix6 l = Matrix6::Identity() * .09;
  l(3, 1) = .02; l(5, 2) = -.03;
  Matrix6 q = l * l.transpose();
  const auto a = pose(.4, Eigen::Vector3d(4, -3, 1));
  ov_type::PoseJPL expected_map;
  assign(expected_map, a);
  Eigen::MatrixXd local = StateHelper::get_full_covariance(state);
  const int n = local.rows();
  Eigen::MatrixXd expected = Eigen::MatrixXd::Zero(n + 6, n + 6);
  expected.topLeftCorner(n, n) = local;
  expected.topRightCorner(n, 6) = local * b.transpose();
  expected.bottomLeftCorner(6, n) = b * local;
  expected.bottomRightCorner<6, 6>() = b * local * b.transpose() + q;
  frame.initialize(snapshot, a, b, q, 1., 6.);
  near(StateHelper::get_full_covariance(state), expected, 1e-12, "Augmentation lost full-state correlation");
  require(expected.block(6, n, 9, 6).norm() > .001, "Fixture lacks velocity/bias cross-covariance");
  double max_cov_error = 0;
  double previous_time = 2.;
  int projections = 0;
  for (int step = 1; step <= 60; ++step) {
    const int size = expected.rows();
    Eigen::MatrixXd phi = Eigen::MatrixXd::Identity(15, 15);
    phi.block<3, 3>(3, 6) = Eigen::Matrix3d::Identity() * .05;
    phi.block<3, 3>(6, 12) = Eigen::Matrix3d::Identity() * -.05;
    phi.block<3, 3>(0, 9) = Eigen::Matrix3d::Identity() * -.05;
    Eigen::MatrixXd f = Eigen::MatrixXd::Identity(size, size);
    f.topLeftCorner(15, 15) = phi;
    expected = (f * expected * f.transpose()).eval();
    expected.topLeftCorner(15, 15).diagonal().array() += .0002;
    for (auto s : {state, local_only}) {
      Eigen::VectorXd value = s->_imu->value();
      value.segment<3>(4) += value.segment<3>(7) * .05;
      s->_imu->set_value(value);
      StateHelper::EKFPropagation(s, {s->_imu}, {s->_imu}, phi,
                                 Eigen::MatrixXd::Identity(15, 15) * .0002);
      s->_timestamp = 2. + .05 * step;
    }
    Eigen::MatrixXd h = Eigen::MatrixXd::Zero(4, 15);
    h.block<3, 3>(0, 3).setIdentity();
    h(3, 0) = .3; h(3, 1) = -.1;
    Eigen::Vector4d residual(.003, -.002, .001, .0003);
    Eigen::Matrix4d r = Eigen::Matrix4d::Identity() * .02;
    Eigen::MatrixXd full_h = Eigen::MatrixXd::Zero(4, size);
    full_h.leftCols(15) = h;
    Eigen::MatrixXd innovation = full_h * expected * full_h.transpose() + r;
    Eigen::MatrixXd gain = innovation.llt().solve((expected * full_h.transpose()).transpose()).transpose();
    expected_map.update(gain.bottomRows(6) * residual);
    // Independent Joseph-form reference, versus upstream's subtractive update.
    Eigen::MatrixXd ikh = Eigen::MatrixXd::Identity(size, size) - gain * full_h;
    expected = (ikh * expected * ikh.transpose() + gain * r * gain.transpose()).eval();
    for (auto s : {state, local_only}) StateHelper::EKFUpdate(s, {s->_imu}, h, residual, r);

    if (step == 20) {
      // Evict the query clone, preserving map/current correlations and shifted IDs.
      Eigen::MatrixXd reduced = Eigen::MatrixXd::Zero(size - 6, size - 6);
      reduced.topLeftCorner(15, 15) = expected.topLeftCorner(15, 15);
      reduced.topRightCorner(15, 6) = expected.topRightCorner(15, 6);
      reduced.bottomLeftCorner(6, 15) = expected.bottomLeftCorner(6, 15);
      reduced.bottomRightCorner<6, 6>() = expected.bottomRightCorner<6, 6>();
      expected = reduced;
      for (auto s : {state, local_only}) {
        StateHelper::marginalize(s, s->_clones_IMU.at(1.));
        s->_clones_IMU.erase(1.);
      }
    }
    near(state->_imu->value(), local_only->_imu->value(), 1e-12, "Map frame changed local mean");
    near(StateHelper::get_marginal_covariance(state, {state->_imu}),
         StateHelper::get_marginal_covariance(local_only, {local_only->_imu}), 1e-12,
         "Map frame changed local covariance");
    Eigen::MatrixXd actual = StateHelper::get_full_covariance(state);
    max_cov_error = std::max(max_cov_error, (actual - expected).cwiseAbs().maxCoeff());
    near(actual, expected, 1e-11, "Native propagation/update disagrees with full joint Gaussian reference");
    auto output = frame.project();
    require(output.timestamp_s > previous_time && output.observation_timestamp_s == 1.
            && output.map_age_s == output.timestamp_s - 1., "Original/current map times lost");
    require(!output.global_pose_authority && !output.covariance_calibrated
            && !output.px4_publication_enabled, "Native component granted authority");
    previous_time = output.timestamp_s;
    ++projections;
    Eigen::Matrix4d expected_y = CorrelatedMapFrame::transform(expected_map).inverse()
                                 * CorrelatedMapFrame::transform(*state->_imu->pose());
    near(output.map_T_imu, expected_y, 1e-11, "Correlated map mean was frozen or misprojected");
    Eigen::Matrix<double, 12, 12> joint;
    joint.topLeftCorner<6, 6>() = expected.bottomRightCorner<6, 6>();
    joint.bottomRightCorner<6, 6>() = expected.topLeftCorner<6, 6>();
    joint.topRightCorner<6, 6>() = expected.bottomLeftCorner(6, expected.cols() - 6).leftCols(6);
    joint.bottomLeftCorner<6, 6>() = joint.topRightCorner<6, 6>().transpose();
    auto j = CorrelatedMapFrame::projection_jacobian(expected_map, *state->_imu->pose());
    near(output.covariance, j * joint * j.transpose(), 1e-10, "Projection covariance lost correlations");
    joint.topRightCorner<6, 6>().setZero();
    joint.bottomLeftCorner<6, 6>().setZero();
    require((output.covariance - j * joint * j.transpose()).norm() > .001,
            "Fixture does not detect independent-map/current simplification");
  }
  auto before = StateHelper::get_marginal_covariance(state, {state->_imu});
  frame.clear();
  require(state->max_covariance_size() == 15, "Map release leaked native state");
  near(before, StateHelper::get_full_covariance(state), 1e-14, "Release changed local marginal");
  require(projections == 60, "Missing advancing native projections");
  std::cout << "60/60 native EKF projections; max joint covariance error: " << max_cov_error << '\n';
}

Eigen::Matrix<double, 6, 1> pose_error(const Eigen::Matrix4d &nominal,
                                    const Eigen::Matrix4d &changed, bool right_translation) {
  Eigen::Matrix<double, 6, 1> error;
  Eigen::AngleAxisd angle(nominal.topLeftCorner<3, 3>().transpose() * changed.topLeftCorner<3, 3>());
  error.head<3>() = angle.angle() * angle.axis();
  error.tail<3>() = changed.topRightCorner<3, 1>() - nominal.topRightCorner<3, 1>();
  if (right_translation) error.tail<3>() = nominal.topLeftCorner<3, 3>().transpose() * error.tail<3>().eval();
  return error;
}

void single_fix_geometry() {
  // Only this synthetic fixture assumes map-camera noise independent of native
  // errors. Production inputs must derive their full shared-error regression.
  auto state = make_state();
  auto anchor = state->_clones_IMU.at(1.);
  auto current = state->_imu->pose();
  ov_type::PoseJPL z;
  assign(z, pose(.9, Eigen::Vector3d(21, -9, 13)));
  const Eigen::Matrix4d c = pose(-.8, Eigen::Vector3d(.2, -.1, .04));
  const Eigen::Matrix4d x = CorrelatedMapFrame::transform(*anchor);
  const Eigen::Matrix4d world_camera = CorrelatedMapFrame::transform(z);
  const Eigen::Matrix4d a = x * c * world_camera.inverse();
  Matrix6 ja, jz;
  for (int which = 0; which < 2; ++which) {
    for (int axis = 0; axis < 6; ++axis) {
      Eigen::Matrix<double, 6, 1> errors[2];
      for (int sign = 0; sign < 2; ++sign) {
        auto variable = std::dynamic_pointer_cast<ov_type::PoseJPL>((which == 0 ? anchor->clone() : z.clone()));
        Eigen::VectorXd d = Eigen::VectorXd::Zero(6);
        d(axis) = (sign ? 1. : -1.) * 1e-6;
        variable->update(d);
        Eigen::Matrix4d changed = which == 0
            ? (CorrelatedMapFrame::transform(*variable) * c * world_camera.inverse()).eval()
            : (x * c * CorrelatedMapFrame::transform(*variable).inverse()).eval();
        errors[sign] = pose_error(a, changed, false);
      }
      (which == 0 ? ja : jz).col(axis) = (errors[1] - errors[0]) / 2e-6;
    }
  }
  CorrelatedMapFrame frame(state);
  auto snapshot = frame.capture({state->_imu, anchor});
  Eigen::MatrixXd b = Eigen::MatrixXd::Zero(6, snapshot.dimension());
  b.rightCols(6) = ja;
  Matrix6 pz = Matrix6::Identity() * .004;
  pz.bottomRightCorner<3, 3>() *= 10;
  const Matrix6 q = jz * pz * jz.transpose();
  const Eigen::Matrix<double, 12, 12> native_joint = StateHelper::get_marginal_covariance(state, {anchor, current});
  frame.initialize(snapshot, a, b, q, 1., 5.);
  auto output = frame.project();
  const Eigen::Matrix4d y = world_camera * c.inverse() * x.inverse() * CorrelatedMapFrame::transform(*current);
  Eigen::Matrix<double, 6, 18> h;
  for (int which = 0; which < 3; ++which) {
    for (int axis = 0; axis < 6; ++axis) {
      Eigen::Matrix<double, 6, 1> errors[2];
      for (int sign = 0; sign < 2; ++sign) {
        auto qa = std::dynamic_pointer_cast<ov_type::PoseJPL>(anchor->clone());
        auto qx = std::dynamic_pointer_cast<ov_type::PoseJPL>(current->clone());
        auto qz = std::dynamic_pointer_cast<ov_type::PoseJPL>(z.clone());
        Eigen::VectorXd d = Eigen::VectorXd::Zero(6);
        d(axis) = (sign ? 1. : -1.) * 1e-6;
        (which == 0 ? qa : (which == 1 ? qx : qz))->update(d);
        const Eigen::Matrix4d changed = CorrelatedMapFrame::transform(*qz) * c.inverse()
            * CorrelatedMapFrame::transform(*qa).inverse() * CorrelatedMapFrame::transform(*qx);
        errors[sign] = pose_error(y, changed, true);
      }
      h.col(which * 6 + axis) = (errors[1] - errors[0]) / 2e-6;
    }
  }
  Eigen::Matrix<double, 18, 18> p = Eigen::Matrix<double, 18, 18>::Zero();
  p.topLeftCorner<12, 12>() = native_joint;
  p.bottomRightCorner<6, 6>() = pz;
  near(output.map_T_imu, y, 1e-12, "Single-fix geometry mean mismatch");
  near(output.covariance, h * p * h.transpose(), 1e-8, "Single-fix geometry covariance mismatch");
  std::cout << "Single-fix camera/extrinsic/anchor/current joint geometry agrees\n";
}
}  // namespace

int main() {
  try {
    jacobians();
    rejects_invalid_initialization();
    continuous_native_updates();
    single_fix_geometry();
    std::cout << "Correlated map frame: shadow-only augmentation, updates, projection and retirement verified\n";
    return 0;
  } catch (const std::exception &e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
