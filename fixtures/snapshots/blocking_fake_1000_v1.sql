select
            '0' as match_key,
            l."unique_id" as join_key_l,
            r."unique_id" as join_key_r
            from __splink__df_concat as l
            inner join __splink__df_concat as r
            on
            (l."first_name" = r."first_name")
            where l."unique_id" < r."unique_id"

             UNION ALL
            select
            '1' as match_key,
            l."unique_id" as join_key_l,
            r."unique_id" as join_key_r
            from __splink__df_concat as l
            inner join __splink__df_concat as r
            on
            (l."surname" = r."surname")
            where l."unique_id" < r."unique_id"
            AND NOT (coalesce((l."first_name" = r."first_name"),false))
             UNION ALL
            select
            '2' as match_key,
            l."unique_id" as join_key_l,
            r."unique_id" as join_key_r
            from __splink__df_concat as l
            inner join __splink__df_concat as r
            on
            (l."dob" = r."dob")
            where l."unique_id" < r."unique_id"
            AND NOT (coalesce((l."first_name" = r."first_name"),false) OR coalesce((l."surname" = r."surname"),false))
             UNION ALL
            select
            '3' as match_key,
            l."unique_id" as join_key_l,
            r."unique_id" as join_key_r
            from __splink__df_concat as l
            inner join __splink__df_concat as r
            on
            (l."email" = r."email")
            where l."unique_id" < r."unique_id"
            AND NOT (coalesce((l."first_name" = r."first_name"),false) OR coalesce((l."surname" = r."surname"),false) OR coalesce((l."dob" = r."dob"),false))
