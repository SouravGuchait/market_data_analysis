-- Initial MySQL 8 schema for Market Data Analytics.
-- Generated from the Django model state on 2026-11-11.
-- Includes Django core/auth/session tables, accounts, exchanges, and JWT blacklist tables.
-- Apply to an empty database. Select the database in your MySQL client before sourcing this file.
-- This records installed migrations as applied; Django migrate will run post_migrate hooks
-- (content types and permissions) without trying to recreate these tables.
-- No users, passwords, tokens, or other application data are seeded.

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE TABLE `django_admin_log` (`id` integer AUTO_INCREMENT NOT NULL PRIMARY KEY, `action_time` datetime(6) NOT NULL, `user_id` integer NOT NULL, `content_type_id` integer NULL, `object_id` longtext NULL, `object_repr` varchar(200) NOT NULL, `action_flag` smallint UNSIGNED NOT NULL CHECK (`action_flag` >= 0), `change_message` longtext NOT NULL);

CREATE TABLE `auth_permission` (`id` integer AUTO_INCREMENT NOT NULL PRIMARY KEY, `name` varchar(255) NOT NULL, `content_type_id` integer NOT NULL, `codename` varchar(100) NOT NULL);

CREATE TABLE `auth_group` (`id` integer AUTO_INCREMENT NOT NULL PRIMARY KEY, `name` varchar(150) NOT NULL UNIQUE);

CREATE TABLE `auth_group_permissions` (`id` integer AUTO_INCREMENT NOT NULL PRIMARY KEY, `group_id` integer NOT NULL, `permission_id` integer NOT NULL);

CREATE TABLE `auth_user` (`id` integer AUTO_INCREMENT NOT NULL PRIMARY KEY, `password` varchar(128) NOT NULL, `last_login` datetime(6) NULL, `is_superuser` bool NOT NULL, `username` varchar(150) NOT NULL UNIQUE, `first_name` varchar(150) NULL, `last_name` varchar(150) NULL, `email` varchar(254) NOT NULL UNIQUE, `is_staff` bool NOT NULL, `is_active` bool NOT NULL, `date_joined` datetime(6) NOT NULL);

CREATE TABLE `auth_user_groups` (`id` integer AUTO_INCREMENT NOT NULL PRIMARY KEY, `user_id` integer NOT NULL, `group_id` integer NOT NULL);

CREATE TABLE `auth_user_user_permissions` (`id` integer AUTO_INCREMENT NOT NULL PRIMARY KEY, `user_id` integer NOT NULL, `permission_id` integer NOT NULL);

CREATE TABLE `django_content_type` (`id` integer AUTO_INCREMENT NOT NULL PRIMARY KEY, `app_label` varchar(100) NOT NULL, `model` varchar(100) NOT NULL);

CREATE TABLE `django_session` (`session_key` varchar(40) NOT NULL PRIMARY KEY, `session_data` longtext NOT NULL, `expire_date` datetime(6) NOT NULL);

CREATE TABLE `token_blacklist_outstandingtoken` (`id` bigint AUTO_INCREMENT NOT NULL PRIMARY KEY, `user_id` integer NULL, `jti` varchar(255) NOT NULL UNIQUE, `token` longtext NOT NULL, `created_at` datetime(6) NULL, `expires_at` datetime(6) NOT NULL);

CREATE TABLE `token_blacklist_blacklistedtoken` (`id` bigint AUTO_INCREMENT NOT NULL PRIMARY KEY, `token_id` bigint NOT NULL UNIQUE, `blacklisted_at` datetime(6) NOT NULL);

CREATE TABLE `accounts_passwordresettoken` (`id` bigint AUTO_INCREMENT NOT NULL PRIMARY KEY, `user_id` integer NOT NULL, `token_hash` varchar(64) NOT NULL UNIQUE, `created_at` datetime(6) NOT NULL, `is_used` bool NOT NULL);

CREATE TABLE `exchanges_exchange` (`id` bigint AUTO_INCREMENT NOT NULL PRIMARY KEY, `created_at` datetime(6) NOT NULL, `updated_at` datetime(6) NOT NULL, `code` varchar(32) NOT NULL UNIQUE, `name` varchar(128) NOT NULL, `timezone` varchar(64) NOT NULL, `is_active` bool NOT NULL);

CREATE TABLE `django_migrations` (`id` bigint AUTO_INCREMENT NOT NULL PRIMARY KEY, `app` varchar(255) NOT NULL, `name` varchar(255) NOT NULL, `applied` datetime(6) NOT NULL);

ALTER TABLE `django_admin_log` ADD CONSTRAINT `django_admin_log_user_id_c564eba6_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`);

ALTER TABLE `django_admin_log` ADD CONSTRAINT `django_admin_log_content_type_id_c4bce8eb_fk_django_co` FOREIGN KEY (`content_type_id`) REFERENCES `django_content_type` (`id`);

CREATE INDEX `django_admin_log_user_id_c564eba6` ON `django_admin_log` (`user_id`);

CREATE INDEX `django_admin_log_content_type_id_c4bce8eb` ON `django_admin_log` (`content_type_id`);

ALTER TABLE `auth_permission` ADD CONSTRAINT `auth_permission_content_type_id_codename_01ab375a_uniq` UNIQUE (`content_type_id`, `codename`);

ALTER TABLE `auth_permission` ADD CONSTRAINT `auth_permission_content_type_id_2f476e4b_fk_django_co` FOREIGN KEY (`content_type_id`) REFERENCES `django_content_type` (`id`);

CREATE INDEX `auth_permission_content_type_id_2f476e4b` ON `auth_permission` (`content_type_id`);

ALTER TABLE `auth_group_permissions` ADD CONSTRAINT `auth_group_permissions_group_id_permission_id_0cd325b0_uniq` UNIQUE (`group_id`, `permission_id`);

ALTER TABLE `auth_group_permissions` ADD CONSTRAINT `auth_group_permissions_group_id_b120cbf9_fk_auth_group_id` FOREIGN KEY (`group_id`) REFERENCES `auth_group` (`id`);

ALTER TABLE `auth_group_permissions` ADD CONSTRAINT `auth_group_permissio_permission_id_84c5c92e_fk_auth_perm` FOREIGN KEY (`permission_id`) REFERENCES `auth_permission` (`id`);

CREATE INDEX `auth_group_permissions_group_id_b120cbf9` ON `auth_group_permissions` (`group_id`);

CREATE INDEX `auth_group_permissions_permission_id_84c5c92e` ON `auth_group_permissions` (`permission_id`);

ALTER TABLE `auth_user_groups` ADD CONSTRAINT `auth_user_groups_user_id_group_id_94350c0c_uniq` UNIQUE (`user_id`, `group_id`);

ALTER TABLE `auth_user_groups` ADD CONSTRAINT `auth_user_groups_user_id_6a12ed8b_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`);

ALTER TABLE `auth_user_groups` ADD CONSTRAINT `auth_user_groups_group_id_97559544_fk_auth_group_id` FOREIGN KEY (`group_id`) REFERENCES `auth_group` (`id`);

CREATE INDEX `auth_user_groups_user_id_6a12ed8b` ON `auth_user_groups` (`user_id`);

CREATE INDEX `auth_user_groups_group_id_97559544` ON `auth_user_groups` (`group_id`);

ALTER TABLE `auth_user_user_permissions` ADD CONSTRAINT `auth_user_user_permissions_user_id_permission_id_14a6b632_uniq` UNIQUE (`user_id`, `permission_id`);

ALTER TABLE `auth_user_user_permissions` ADD CONSTRAINT `auth_user_user_permissions_user_id_a95ead1b_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`);

ALTER TABLE `auth_user_user_permissions` ADD CONSTRAINT `auth_user_user_permi_permission_id_1fbb5f2c_fk_auth_perm` FOREIGN KEY (`permission_id`) REFERENCES `auth_permission` (`id`);

CREATE INDEX `auth_user_user_permissions_user_id_a95ead1b` ON `auth_user_user_permissions` (`user_id`);

CREATE INDEX `auth_user_user_permissions_permission_id_1fbb5f2c` ON `auth_user_user_permissions` (`permission_id`);

ALTER TABLE `django_content_type` ADD CONSTRAINT `django_content_type_app_label_model_76bd3d3b_uniq` UNIQUE (`app_label`, `model`);

CREATE INDEX `django_session_expire_date_a5c62663` ON `django_session` (`expire_date`);

ALTER TABLE `token_blacklist_outstandingtoken` ADD CONSTRAINT `token_blacklist_outs_user_id_83bc629a_fk_auth_user` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`);

CREATE INDEX `token_blacklist_outstandingtoken_user_id_83bc629a` ON `token_blacklist_outstandingtoken` (`user_id`);

ALTER TABLE `token_blacklist_blacklistedtoken` ADD CONSTRAINT `token_blacklist_blac_token_id_3cc7fe56_fk_token_bla` FOREIGN KEY (`token_id`) REFERENCES `token_blacklist_outstandingtoken` (`id`);

ALTER TABLE `accounts_passwordresettoken` ADD CONSTRAINT `accounts_passwordresettoken_user_id_2789bc5c_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`);

CREATE INDEX `accounts_passwordresettoken_user_id_2789bc5c` ON `accounts_passwordresettoken` (`user_id`);

SET FOREIGN_KEY_CHECKS = 1;

-- Migration state for the schema above.
INSERT INTO `django_migrations` (`app`, `name`, `applied`) VALUES
  ('accounts', '0001_initial', UTC_TIMESTAMP(6)),
  ('admin', '0001_initial', UTC_TIMESTAMP(6)),
  ('admin', '0002_logentry_remove_auto_add', UTC_TIMESTAMP(6)),
  ('admin', '0003_logentry_add_action_flag_choices', UTC_TIMESTAMP(6)),
  ('auth', '0001_initial', UTC_TIMESTAMP(6)),
  ('auth', '0002_alter_permission_name_max_length', UTC_TIMESTAMP(6)),
  ('auth', '0003_alter_user_email_max_length', UTC_TIMESTAMP(6)),
  ('auth', '0004_alter_user_username_opts', UTC_TIMESTAMP(6)),
  ('auth', '0005_alter_user_last_login_null', UTC_TIMESTAMP(6)),
  ('auth', '0006_require_contenttypes_0002', UTC_TIMESTAMP(6)),
  ('auth', '0007_alter_validators_add_error_messages', UTC_TIMESTAMP(6)),
  ('auth', '0008_alter_user_username_max_length', UTC_TIMESTAMP(6)),
  ('auth', '0009_alter_user_last_name_max_length', UTC_TIMESTAMP(6)),
  ('auth', '0010_alter_group_name_max_length', UTC_TIMESTAMP(6)),
  ('auth', '0011_update_proxy_permissions', UTC_TIMESTAMP(6)),
  ('auth', '0012_alter_user_first_name_max_length', UTC_TIMESTAMP(6)),
  ('contenttypes', '0001_initial', UTC_TIMESTAMP(6)),
  ('contenttypes', '0002_remove_content_type_name', UTC_TIMESTAMP(6)),
  ('exchanges', '0001_initial', UTC_TIMESTAMP(6)),
  ('sessions', '0001_initial', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0001_initial', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0002_outstandingtoken_jti_hex', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0003_auto_20171017_2007', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0004_auto_20171017_2013', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0005_remove_outstandingtoken_jti', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0006_auto_20171017_2113', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0007_auto_20171017_2214', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0008_migrate_to_bigautofield', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0010_fix_migrate_to_bigautofield', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0011_linearizes_history', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0012_alter_outstandingtoken_user', UTC_TIMESTAMP(6)),
  ('token_blacklist', '0013_alter_blacklistedtoken_options_and_more', UTC_TIMESTAMP(6));
