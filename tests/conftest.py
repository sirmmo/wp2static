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
        "(2,10,'_thumbnail_id','20'),"
        # -- nav_menu_item #100: Home — custom link to the site root
        "(10,100,'_menu_item_type','custom'),"
        "(11,100,'_menu_item_menu_item_parent','0'),"
        "(12,100,'_menu_item_object_id','100'),"
        "(13,100,'_menu_item_object','custom'),"
        "(14,100,'_menu_item_url','https://example.com/'),"
        "(15,100,'_menu_item_target',''),"
        # -- nav_menu_item #101: About — linked to page 11
        "(20,101,'_menu_item_type','post_type'),"
        "(21,101,'_menu_item_menu_item_parent','0'),"
        "(22,101,'_menu_item_object_id','11'),"
        "(23,101,'_menu_item_object','page'),"
        "(24,101,'_menu_item_target','_blank'),"
        # -- nav_menu_item #102: child of About — linked to post 10
        "(30,102,'_menu_item_type','post_type'),"
        "(31,102,'_menu_item_menu_item_parent','101'),"
        "(32,102,'_menu_item_object_id','10'),"
        "(33,102,'_menu_item_object','post');\n"
        # -- Menu term + taxonomy + item relationships
        "INSERT INTO `wp_terms` VALUES (5,'Primary','primary-menu',0);\n"
        "INSERT INTO `wp_term_taxonomy` VALUES (5,5,'nav_menu','',0,3);\n"
        "INSERT INTO `wp_term_relationships` VALUES "
        "(100,5,0),(101,5,0),(102,5,0);\n"
        # -- Three nav_menu_item posts; post_title 'Home' is the custom label.
        #    The two post_type items leave post_title empty so the loader
        #    must fall back to the linked object's title.
        "INSERT INTO `wp_posts` VALUES "
        "(100,1,'2024-03-01 10:00:00','2024-03-01 10:00:00',"
        "'','Home','','publish','closed','closed','','home-item','','',"
        "'2024-03-01 10:00:00','2024-03-01 10:00:00','',"
        "0,'',1,'nav_menu_item','',0),"
        "(101,1,'2024-03-01 10:00:00','2024-03-01 10:00:00',"
        "'','','','publish','closed','closed','','about-item','','',"
        "'2024-03-01 10:00:00','2024-03-01 10:00:00','',"
        "0,'',2,'nav_menu_item','',0),"
        "(102,1,'2024-03-01 10:00:00','2024-03-01 10:00:00',"
        "'','','','publish','closed','closed','','post-item','','',"
        "'2024-03-01 10:00:00','2024-03-01 10:00:00','',"
        "0,'',3,'nav_menu_item','',0);\n"
        # -- theme_mods_kale maps the 'header' slot to menu term 5.
        "INSERT INTO `wp_options` VALUES "
        "(99,'theme_mods_kale',"
        "'a:1:{s:18:\"nav_menu_locations\";a:1:{s:6:\"header\";i:5;}}',"
        "'yes');\n"
        "INSERT INTO `wp_comments` VALUES (1,10,'not parsed');\n",
        encoding="utf-8",
    )
    return sql
