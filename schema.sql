-- MySQL dump 10.13  Distrib 8.0.46, for Linux (x86_64)
--
-- Host: localhost    Database: mda_backend
-- ------------------------------------------------------
-- Server version	8.0.46

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `accounts_passwordresettoken`
--

DROP TABLE IF EXISTS `accounts_passwordresettoken`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `accounts_passwordresettoken` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `token_hash` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `is_used` tinyint(1) NOT NULL,
  `user_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `token_hash` (`token_hash`),
  KEY `accounts_passwordresettoken_user_id_2789bc5c_fk_auth_user_id` (`user_id`),
  CONSTRAINT `accounts_passwordresettoken_user_id_2789bc5c_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `auth_group`
--

DROP TABLE IF EXISTS `auth_group`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `auth_group` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `auth_group_permissions`
--

DROP TABLE IF EXISTS `auth_group_permissions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `auth_group_permissions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `group_id` int NOT NULL,
  `permission_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `auth_group_permissions_group_id_permission_id_0cd325b0_uniq` (`group_id`,`permission_id`),
  KEY `auth_group_permissio_permission_id_84c5c92e_fk_auth_perm` (`permission_id`),
  CONSTRAINT `auth_group_permissio_permission_id_84c5c92e_fk_auth_perm` FOREIGN KEY (`permission_id`) REFERENCES `auth_permission` (`id`),
  CONSTRAINT `auth_group_permissions_group_id_b120cbf9_fk_auth_group_id` FOREIGN KEY (`group_id`) REFERENCES `auth_group` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `auth_permission`
--

DROP TABLE IF EXISTS `auth_permission`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `auth_permission` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `content_type_id` int NOT NULL,
  `codename` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `auth_permission_content_type_id_codename_01ab375a_uniq` (`content_type_id`,`codename`),
  CONSTRAINT `auth_permission_content_type_id_2f476e4b_fk_django_co` FOREIGN KEY (`content_type_id`) REFERENCES `django_content_type` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=121 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `auth_user`
--

DROP TABLE IF EXISTS `auth_user`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `auth_user` (
  `id` int NOT NULL AUTO_INCREMENT,
  `password` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_login` datetime(6) DEFAULT NULL,
  `is_superuser` tinyint(1) NOT NULL,
  `username` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  `first_name` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_name` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  `email` varchar(254) COLLATE utf8mb4_unicode_ci NOT NULL,
  `is_staff` tinyint(1) NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `date_joined` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB AUTO_INCREMENT=2 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `auth_user_groups`
--

DROP TABLE IF EXISTS `auth_user_groups`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `auth_user_groups` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `group_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `auth_user_groups_user_id_group_id_94350c0c_uniq` (`user_id`,`group_id`),
  KEY `auth_user_groups_group_id_97559544_fk_auth_group_id` (`group_id`),
  CONSTRAINT `auth_user_groups_group_id_97559544_fk_auth_group_id` FOREIGN KEY (`group_id`) REFERENCES `auth_group` (`id`),
  CONSTRAINT `auth_user_groups_user_id_6a12ed8b_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `auth_user_user_permissions`
--

DROP TABLE IF EXISTS `auth_user_user_permissions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `auth_user_user_permissions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `permission_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `auth_user_user_permissions_user_id_permission_id_14a6b632_uniq` (`user_id`,`permission_id`),
  KEY `auth_user_user_permi_permission_id_1fbb5f2c_fk_auth_perm` (`permission_id`),
  CONSTRAINT `auth_user_user_permi_permission_id_1fbb5f2c_fk_auth_perm` FOREIGN KEY (`permission_id`) REFERENCES `auth_permission` (`id`),
  CONSTRAINT `auth_user_user_permissions_user_id_a95ead1b_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `django_admin_log`
--

DROP TABLE IF EXISTS `django_admin_log`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `django_admin_log` (
  `id` int NOT NULL AUTO_INCREMENT,
  `action_time` datetime(6) NOT NULL,
  `object_id` longtext COLLATE utf8mb4_unicode_ci,
  `object_repr` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL,
  `action_flag` smallint unsigned NOT NULL,
  `change_message` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `content_type_id` int DEFAULT NULL,
  `user_id` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `django_admin_log_content_type_id_c4bce8eb_fk_django_co` (`content_type_id`),
  KEY `django_admin_log_user_id_c564eba6_fk_auth_user_id` (`user_id`),
  CONSTRAINT `django_admin_log_content_type_id_c4bce8eb_fk_django_co` FOREIGN KEY (`content_type_id`) REFERENCES `django_content_type` (`id`),
  CONSTRAINT `django_admin_log_user_id_c564eba6_fk_auth_user_id` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `django_admin_log_chk_1` CHECK ((`action_flag` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `django_content_type`
--

DROP TABLE IF EXISTS `django_content_type`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `django_content_type` (
  `id` int NOT NULL AUTO_INCREMENT,
  `app_label` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `model` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `django_content_type_app_label_model_76bd3d3b_uniq` (`app_label`,`model`)
) ENGINE=InnoDB AUTO_INCREMENT=31 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `django_migrations`
--

DROP TABLE IF EXISTS `django_migrations`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `django_migrations` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `app` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `applied` datetime(6) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=47 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `django_session`
--

DROP TABLE IF EXISTS `django_session`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `django_session` (
  `session_key` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL,
  `session_data` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `expire_date` datetime(6) NOT NULL,
  PRIMARY KEY (`session_key`),
  KEY `django_session_expire_date_a5c62663` (`expire_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `exchanges_exchange`
--

DROP TABLE IF EXISTS `exchanges_exchange`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `exchanges_exchange` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `code` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `timezone` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `code` (`code`)
) ENGINE=InnoDB AUTO_INCREMENT=9 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_asset_classes`
--

DROP TABLE IF EXISTS `mda_asset_classes`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_asset_classes` (
  `code` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `position` smallint unsigned NOT NULL,
  PRIMARY KEY (`code`),
  UNIQUE KEY `position` (`position`),
  CONSTRAINT `mda_asset_classes_chk_1` CHECK ((`position` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_audit_events`
--

DROP TABLE IF EXISTS `mda_audit_events`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_audit_events` (
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `action` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `resource_type` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `resource_id` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `request_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `source_ip` char(39) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `metadata` json NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `actor_id` int DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_audit_actor_time` (`actor_id`,`created_at` DESC),
  KEY `idx_audit_action_time` (`action`,`created_at` DESC),
  KEY `idx_audit_resource_time` (`resource_type`,`resource_id`,`created_at` DESC),
  KEY `idx_audit_status_time` (`status`,`created_at` DESC),
  KEY `idx_audit_created_at` (`created_at` DESC),
  CONSTRAINT `mda_audit_events_actor_id_2cf0a5c6_fk_auth_user_id` FOREIGN KEY (`actor_id`) REFERENCES `auth_user` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_currencies`
--

DROP TABLE IF EXISTS `mda_currencies`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_currencies` (
  `code` varchar(3) COLLATE utf8mb4_unicode_ci NOT NULL,
  `position` smallint unsigned NOT NULL,
  PRIMARY KEY (`code`),
  UNIQUE KEY `position` (`position`),
  CONSTRAINT `mda_currencies_chk_1` CHECK ((`position` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_daily_ohlcv`
--

DROP TABLE IF EXISTS `mda_daily_ohlcv`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_daily_ohlcv` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `trading_date` date NOT NULL,
  `timestamp` datetime(6) NOT NULL,
  `open` decimal(20,6) NOT NULL,
  `high` decimal(20,6) NOT NULL,
  `low` decimal(20,6) NOT NULL,
  `close` decimal(20,6) NOT NULL,
  `adjusted_close` decimal(20,6) DEFAULT NULL,
  `volume` bigint unsigned NOT NULL,
  `instrument_id` bigint NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_instrument_trading_date` (`instrument_id`,`trading_date`),
  KEY `idx_ohlcv_date` (`trading_date`),
  CONSTRAINT `mda_daily_ohlcv_instrument_id_08806e3d_fk_mda_instruments_id` FOREIGN KEY (`instrument_id`) REFERENCES `mda_instruments` (`id`),
  CONSTRAINT `mda_daily_ohlcv_chk_1` CHECK ((`volume` >= 0)),
  CONSTRAINT `ohlcv_close_positive` CHECK ((`close` > 0)),
  CONSTRAINT `ohlcv_high_bounds` CHECK (((`high` >= `open`) and (`high` >= `close`))),
  CONSTRAINT `ohlcv_high_gte_low` CHECK ((`high` >= `low`)),
  CONSTRAINT `ohlcv_high_positive` CHECK ((`high` > 0)),
  CONSTRAINT `ohlcv_low_bounds` CHECK (((`low` <= `open`) and (`low` <= `close`))),
  CONSTRAINT `ohlcv_low_positive` CHECK ((`low` > 0)),
  CONSTRAINT `ohlcv_open_positive` CHECK ((`open` > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_generator_symbols`
--

DROP TABLE IF EXISTS `mda_generator_symbols`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_generator_symbols` (
  `symbol` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_ingestion_batches`
--

DROP TABLE IF EXISTS `mda_ingestion_batches`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_ingestion_batches` (
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `batch_number` int unsigned NOT NULL,
  `source_start_row` int unsigned NOT NULL,
  `source_end_row` int unsigned NOT NULL,
  `rows_read` int unsigned NOT NULL,
  `accepted_rows` int unsigned NOT NULL,
  `rejected_rows` int unsigned NOT NULL,
  `duplicate_rows` int unsigned NOT NULL,
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `completed_at` datetime(6) DEFAULT NULL,
  `ingestion_id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_ingestion_batch_number` (`ingestion_id`,`batch_number`),
  KEY `idx_ing_batch_status` (`ingestion_id`,`status`),
  CONSTRAINT `mda_ingestion_batches_ingestion_id_3902b14b_fk_mda_ingestions_id` FOREIGN KEY (`ingestion_id`) REFERENCES `mda_ingestions` (`id`),
  CONSTRAINT `mda_ingestion_batches_chk_1` CHECK ((`batch_number` >= 0)),
  CONSTRAINT `mda_ingestion_batches_chk_2` CHECK ((`source_start_row` >= 0)),
  CONSTRAINT `mda_ingestion_batches_chk_3` CHECK ((`source_end_row` >= 0)),
  CONSTRAINT `mda_ingestion_batches_chk_4` CHECK ((`rows_read` >= 0)),
  CONSTRAINT `mda_ingestion_batches_chk_5` CHECK ((`accepted_rows` >= 0)),
  CONSTRAINT `mda_ingestion_batches_chk_6` CHECK ((`rejected_rows` >= 0)),
  CONSTRAINT `mda_ingestion_batches_chk_7` CHECK ((`duplicate_rows` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_ingestion_object_states`
--

DROP TABLE IF EXISTS `mda_ingestion_object_states`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_ingestion_object_states` (
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `object_key` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `object_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `observed_size_bytes` bigint unsigned NOT NULL,
  `observed_modified_at` datetime(6) DEFAULT NULL,
  `stable_scan_count` int unsigned NOT NULL,
  `is_historical` tinyint(1) NOT NULL,
  `first_seen_at` datetime(6) NOT NULL,
  `last_seen_at` datetime(6) NOT NULL,
  `last_content_hash` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_processed_content_hash` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_processed_modified_at` datetime(6) DEFAULT NULL,
  `last_ingested_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `rule_id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_ingestion_object_state` (`rule_id`,`object_key`),
  KEY `idx_ing_object_rule_seen` (`rule_id`,`last_seen_at`),
  CONSTRAINT `mda_ingestion_object_rule_id_ce7376e9_fk_mda_inges` FOREIGN KEY (`rule_id`) REFERENCES `mda_ingestion_rules` (`id`),
  CONSTRAINT `mda_ingestion_object_states_chk_1` CHECK ((`observed_size_bytes` >= 0)),
  CONSTRAINT `mda_ingestion_object_states_chk_2` CHECK ((`stable_scan_count` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_ingestion_rules`
--

DROP TABLE IF EXISTS `mda_ingestion_rules`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_ingestion_rules` (
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `storage_backend` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `location_reference` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `file_pattern` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `is_enabled` tinyint(1) NOT NULL,
  `supported_formats` json NOT NULL,
  `schema_mapping` json NOT NULL,
  `instrument_mapping` json NOT NULL,
  `exchange_mapping` json NOT NULL,
  `scan_interval_seconds` int unsigned NOT NULL,
  `stability_policy` json NOT NULL,
  `initial_watermark` datetime(6) DEFAULT NULL,
  `last_scan_watermark` datetime(6) DEFAULT NULL,
  `last_scan_started_at` datetime(6) DEFAULT NULL,
  `last_scan_completed_at` datetime(6) DEFAULT NULL,
  `last_scan_status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_scan_error` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_scan_summary` json NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `owner_id` int DEFAULT NULL,
  `chunk_size` int unsigned NOT NULL,
  `data_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `parser_config` json NOT NULL DEFAULT (_utf8mb4'{}'),
  `storage_prefix` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `validation_rule_set_id` char(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ing_rule_enabled_scan` (`is_enabled`,`last_scan_completed_at`),
  KEY `mda_ingestion_rules_owner_id_683d3f84_fk_auth_user_id` (`owner_id`),
  CONSTRAINT `mda_ingestion_rules_owner_id_683d3f84_fk_auth_user_id` FOREIGN KEY (`owner_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `ingestion_rule_chunk_size_bounds` CHECK (((`chunk_size` >= 1) and (`chunk_size` <= 1000000))),
  CONSTRAINT `ingestion_rule_data_type_known` CHECK (((`data_type` = _utf8mb4'') or (`data_type` in (_utf8mb4'DAILY_OHLCV',_utf8mb4'TICK_DATA')))),
  CONSTRAINT `ingestion_rule_interval_positive` CHECK ((`scan_interval_seconds` >= 1)),
  CONSTRAINT `mda_ingestion_rules_chk_1` CHECK ((`scan_interval_seconds` >= 0)),
  CONSTRAINT `mda_ingestion_rules_chk_2` CHECK ((`chunk_size` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_ingestion_scans`
--

DROP TABLE IF EXISTS `mda_ingestion_scans`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_ingestion_scans` (
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `discovered_count` int unsigned NOT NULL,
  `queued_count` int unsigned NOT NULL,
  `skipped_count` int unsigned NOT NULL,
  `failed_count` int unsigned NOT NULL,
  `started_at` datetime(6) NOT NULL,
  `completed_at` datetime(6) DEFAULT NULL,
  `created_at` datetime(6) NOT NULL,
  `owner_id` int NOT NULL,
  `rule_id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  KEY `mda_ingestion_scans_rule_id_e49c4410_fk_mda_ingestion_rules_id` (`rule_id`),
  KEY `idx_ing_scan_owner_time` (`owner_id`,`started_at` DESC),
  CONSTRAINT `mda_ingestion_scans_owner_id_0e6309ae_fk_auth_user_id` FOREIGN KEY (`owner_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `mda_ingestion_scans_rule_id_e49c4410_fk_mda_ingestion_rules_id` FOREIGN KEY (`rule_id`) REFERENCES `mda_ingestion_rules` (`id`),
  CONSTRAINT `mda_ingestion_scans_chk_1` CHECK ((`discovered_count` >= 0)),
  CONSTRAINT `mda_ingestion_scans_chk_2` CHECK ((`queued_count` >= 0)),
  CONSTRAINT `mda_ingestion_scans_chk_3` CHECK ((`skipped_count` >= 0)),
  CONSTRAINT `mda_ingestion_scans_chk_4` CHECK ((`failed_count` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_ingestion_state_transitions`
--

DROP TABLE IF EXISTS `mda_ingestion_state_transitions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_ingestion_state_transitions` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `from_state` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `to_state` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `reason` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `metadata` json NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `ingestion_id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_ing_transition_ing_time` (`ingestion_id`,`created_at`),
  CONSTRAINT `mda_ingestion_state__ingestion_id_7427c563_fk_mda_inges` FOREIGN KEY (`ingestion_id`) REFERENCES `mda_ingestions` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_ingestions`
--

DROP TABLE IF EXISTS `mda_ingestions`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_ingestions` (
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `storage_backend` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `object_key` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `file_name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `file_size_bytes` bigint unsigned NOT NULL,
  `file_modified_at` datetime(6) NOT NULL,
  `content_hash` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `state` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `celery_task_id` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `started_at` datetime(6) DEFAULT NULL,
  `finished_at` datetime(6) DEFAULT NULL,
  `error_summary` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `rule_id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `accepted_rows` int unsigned NOT NULL,
  `chunk_size` int unsigned NOT NULL,
  `chunks_processed` int unsigned NOT NULL,
  `data_type` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `duplicate_rows` int unsigned NOT NULL,
  `owner_id` int DEFAULT NULL,
  `rejected_rows` int unsigned NOT NULL,
  `reprocess_version` int unsigned NOT NULL,
  `rows_read` int unsigned NOT NULL,
  `validation_rule_set_version` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `storage_file_id` char(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_ingestion_rule_object_hash` (`rule_id`,`object_key`,`content_hash`,`reprocess_version`),
  KEY `idx_ingestion_state_time` (`state`,`created_at` DESC),
  KEY `idx_ingestion_content_hash` (`content_hash`),
  KEY `idx_ingestion_rule_time` (`rule_id`,`created_at` DESC),
  KEY `mda_ingestions_storage_file_id_393d5d3a_fk_mda_storage_files_id` (`storage_file_id`),
  KEY `idx_ingestion_owner_state` (`owner_id`,`state`,`created_at` DESC),
  CONSTRAINT `mda_ingestions_owner_id_7028b695_fk_auth_user_id` FOREIGN KEY (`owner_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `mda_ingestions_rule_id_1c9e0d61_fk_mda_ingestion_rules_id` FOREIGN KEY (`rule_id`) REFERENCES `mda_ingestion_rules` (`id`),
  CONSTRAINT `mda_ingestions_storage_file_id_393d5d3a_fk_mda_storage_files_id` FOREIGN KEY (`storage_file_id`) REFERENCES `mda_storage_files` (`id`),
  CONSTRAINT `ingestion_data_type_known` CHECK (((`data_type` = _utf8mb4'') or (`data_type` in (_utf8mb4'DAILY_OHLCV',_utf8mb4'TICK_DATA')))),
  CONSTRAINT `mda_ingestions_chk_1` CHECK ((`file_size_bytes` >= 0)),
  CONSTRAINT `mda_ingestions_chk_2` CHECK ((`accepted_rows` >= 0)),
  CONSTRAINT `mda_ingestions_chk_3` CHECK ((`chunk_size` >= 0)),
  CONSTRAINT `mda_ingestions_chk_4` CHECK ((`chunks_processed` >= 0)),
  CONSTRAINT `mda_ingestions_chk_5` CHECK ((`duplicate_rows` >= 0)),
  CONSTRAINT `mda_ingestions_chk_6` CHECK ((`rejected_rows` >= 0)),
  CONSTRAINT `mda_ingestions_chk_7` CHECK ((`reprocess_version` >= 0)),
  CONSTRAINT `mda_ingestions_chk_8` CHECK ((`rows_read` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_instruments`
--

DROP TABLE IF EXISTS `mda_instruments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_instruments` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `symbol` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `asset_class` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `currency` varchar(3) COLLATE utf8mb4_unicode_ci NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `exchange_id` bigint NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_exchange_symbol` (`exchange_id`,`symbol`),
  KEY `idx_instrument_symbol` (`symbol`),
  CONSTRAINT `mda_instruments_exchange_id_b863be91_fk_exchanges_exchange_id` FOREIGN KEY (`exchange_id`) REFERENCES `exchanges_exchange` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=11 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_portfolio_instruments`
--

DROP TABLE IF EXISTS `mda_portfolio_instruments`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_portfolio_instruments` (
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `alias` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `instrument_id` bigint NOT NULL,
  `portfolio_id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `exchange_id` bigint NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_portfolio_instrument` (`portfolio_id`,`instrument_id`),
  KEY `mda_portfolio_instru_instrument_id_67c886df_fk_mda_instr` (`instrument_id`),
  KEY `mda_portfolio_instru_exchange_id_fe16ba5b_fk_exchanges` (`exchange_id`),
  CONSTRAINT `mda_portfolio_instru_exchange_id_fe16ba5b_fk_exchanges` FOREIGN KEY (`exchange_id`) REFERENCES `exchanges_exchange` (`id`),
  CONSTRAINT `mda_portfolio_instru_instrument_id_67c886df_fk_mda_instr` FOREIGN KEY (`instrument_id`) REFERENCES `mda_instruments` (`id`),
  CONSTRAINT `mda_portfolio_instru_portfolio_id_ecf4c4be_fk_mda_portf` FOREIGN KEY (`portfolio_id`) REFERENCES `mda_portfolios` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_portfolios`
--

DROP TABLE IF EXISTS `mda_portfolios`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_portfolios` (
  `created_at` datetime(6) NOT NULL,
  `updated_at` datetime(6) NOT NULL,
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `status` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `owner_id` int NOT NULL,
  PRIMARY KEY (`id`),
  KEY `mda_portfolios_owner_id_334f3265_fk_auth_user_id` (`owner_id`),
  CONSTRAINT `mda_portfolios_owner_id_334f3265_fk_auth_user_id` FOREIGN KEY (`owner_id`) REFERENCES `auth_user` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_rejected_rows`
--

DROP TABLE IF EXISTS `mda_rejected_rows`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_rejected_rows` (
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `source_row_number` int unsigned NOT NULL,
  `reason_code` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `field_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `error_message` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `raw_record` json NOT NULL,
  `rejected_at` datetime(6) NOT NULL,
  `batch_id` char(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `ingestion_id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_rejected_ing_row` (`ingestion_id`,`source_row_number`),
  KEY `idx_rejected_ing_reason` (`ingestion_id`,`reason_code`),
  KEY `mda_rejected_rows_batch_id_1eeb0516_fk_mda_ingestion_batches_id` (`batch_id`),
  CONSTRAINT `mda_rejected_rows_batch_id_1eeb0516_fk_mda_ingestion_batches_id` FOREIGN KEY (`batch_id`) REFERENCES `mda_ingestion_batches` (`id`),
  CONSTRAINT `mda_rejected_rows_ingestion_id_0b4c4275_fk_mda_ingestions_id` FOREIGN KEY (`ingestion_id`) REFERENCES `mda_ingestions` (`id`),
  CONSTRAINT `mda_rejected_rows_chk_1` CHECK ((`source_row_number` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_storage_files`
--

DROP TABLE IF EXISTS `mda_storage_files`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_storage_files` (
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `storage_backend` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `storage_key` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `original_filename` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `file_size` bigint unsigned NOT NULL,
  `content_hash` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `detected_format` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL,
  `uploaded_at` datetime(6) NOT NULL,
  `is_active` tinyint(1) NOT NULL,
  `owner_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_storage_file_owner_key` (`owner_id`,`storage_key`),
  KEY `idx_storage_file_owner_time` (`owner_id`,`uploaded_at` DESC),
  KEY `idx_storage_file_hash` (`content_hash`),
  CONSTRAINT `mda_storage_files_owner_id_e3c1929b_fk_auth_user_id` FOREIGN KEY (`owner_id`) REFERENCES `auth_user` (`id`),
  CONSTRAINT `mda_storage_files_chk_1` CHECK ((`file_size` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_tick_columns`
--

DROP TABLE IF EXISTS `mda_tick_columns`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_tick_columns` (
  `name` varchar(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `position` smallint unsigned NOT NULL,
  PRIMARY KEY (`name`),
  UNIQUE KEY `position` (`position`),
  CONSTRAINT `mda_tick_columns_chk_1` CHECK ((`position` >= 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `mda_trades`
--

DROP TABLE IF EXISTS `mda_trades`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `mda_trades` (
  `id` char(32) COLLATE utf8mb4_unicode_ci NOT NULL,
  `event_timestamp` datetime(6) NOT NULL,
  `price` decimal(20,6) NOT NULL,
  `quantity` decimal(20,8) NOT NULL,
  `exchange_trade_id` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL,
  `sequence_number` bigint DEFAULT NULL,
  `side` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL,
  `source_file_reference` varchar(512) COLLATE utf8mb4_unicode_ci NOT NULL,
  `ingestion_timestamp` datetime(6) NOT NULL,
  `deduplication_key` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL,
  `source_metadata` json NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `exchange_id` bigint NOT NULL,
  `ingestion_id` char(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `instrument_id` bigint NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_trade_exchange_instrument_dedupe` (`exchange_id`,`instrument_id`,`deduplication_key`),
  KEY `idx_trade_instrument_time` (`instrument_id`,`event_timestamp`),
  KEY `idx_trade_exchange_time` (`exchange_id`,`event_timestamp`),
  KEY `idx_trade_ingestion` (`ingestion_id`),
  KEY `idx_trade_dedupe_key` (`deduplication_key`),
  CONSTRAINT `mda_trades_exchange_id_fbd336c9_fk_exchanges_exchange_id` FOREIGN KEY (`exchange_id`) REFERENCES `exchanges_exchange` (`id`),
  CONSTRAINT `mda_trades_ingestion_id_6a2b5ee4_fk_mda_ingestions_id` FOREIGN KEY (`ingestion_id`) REFERENCES `mda_ingestions` (`id`),
  CONSTRAINT `mda_trades_instrument_id_dd6cae01_fk_mda_instruments_id` FOREIGN KEY (`instrument_id`) REFERENCES `mda_instruments` (`id`),
  CONSTRAINT `trade_price_positive` CHECK ((`price` > 0)),
  CONSTRAINT `trade_quantity_positive` CHECK ((`quantity` > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `token_blacklist_blacklistedtoken`
--

DROP TABLE IF EXISTS `token_blacklist_blacklistedtoken`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `token_blacklist_blacklistedtoken` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `blacklisted_at` datetime(6) NOT NULL,
  `token_id` bigint NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `token_id` (`token_id`),
  CONSTRAINT `token_blacklist_blacklistedtoken_token_id_3cc7fe56_fk` FOREIGN KEY (`token_id`) REFERENCES `token_blacklist_outstandingtoken` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `token_blacklist_outstandingtoken`
--

DROP TABLE IF EXISTS `token_blacklist_outstandingtoken`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `token_blacklist_outstandingtoken` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `token` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  `created_at` datetime(6) DEFAULT NULL,
  `expires_at` datetime(6) NOT NULL,
  `user_id` int DEFAULT NULL,
  `jti` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `token_blacklist_outstandingtoken_jti_hex_d9bdf6f7_uniq` (`jti`),
  KEY `token_blacklist_outs_user_id_83bc629a_fk_auth_user` (`user_id`),
  CONSTRAINT `token_blacklist_outs_user_id_83bc629a_fk_auth_user` FOREIGN KEY (`user_id`) REFERENCES `auth_user` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=75 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-11-14 12:32:32
