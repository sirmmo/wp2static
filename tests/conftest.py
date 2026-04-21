"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tiny_dump(tmp_path: Path) -> Path:
    """A mysqldump-shaped file with just enough rows to exercise load()."""
    sql = tmp_path / "dump.sql"
    sql.write_text(
        "-- comment line, ignored\n"
        "INSERT INTO `wp_options` VALUES "
        "(1,'siteurl','https://example.com','yes'),"
        "(2,'blogname','Hello','yes'),"
        "(3,'blogdescription','A site','yes'),"
        "(4,'stylesheet','kale','yes'),"
        "(5,'template','kale','yes');\n"
        "INSERT INTO `wp_terms` VALUES (1,'News','news',0);\n"
        "INSERT INTO `wp_term_taxonomy` VALUES (1,1,'category','',0,1);\n"
        "INSERT INTO `wp_term_relationships` VALUES (10,1,0);\n"
        "INSERT INTO `wp_posts` VALUES "
        "(10,1,'2024-01-01 10:00:00','2024-01-01 10:00:00',"
        "'Hello <b>world</b>','First post','','publish',"
        "'open','open','','first-post','','',"
        "'2024-01-02 10:00:00','2024-01-02 10:00:00','',"
        "0,'https://example.com/?p=10',0,'post','',0),"
        "(11,1,'2024-02-01 10:00:00','2024-02-01 10:00:00',"
        "'About page body.','About','','publish',"
        "'open','open','','about','','',"
        "'2024-02-01 10:00:00','2024-02-01 10:00:00','',"
        "0,'https://example.com/about',0,'page','',0),"
        "(20,1,'2024-01-01 10:00:00','2024-01-01 10:00:00',"
        "'','hero.jpg','','inherit',"
        "'open','open','','hero-jpg','','',"
        "'2024-01-01 10:00:00','2024-01-01 10:00:00','',"
        "10,'https://example.com/uploads/hero.jpg',0,"
        "'attachment','image/jpeg',0);\n"
        "INSERT INTO `wp_postmeta` VALUES "
        "(1,20,'_wp_attached_file','2024/01/hero.jpg'),"
        "(2,10,'_thumbnail_id','20');\n"
        "INSERT INTO `wp_comments` VALUES (1,10,'not parsed');\n",
        encoding="utf-8",
    )
    return sql
